"""Загрузчик базы знаний (../knowledge) в системный промпт.

База знаний — постоянный слой, модели меняются (селектор OpenRouter). Это
второй из трёх уровней доставки знания (источник → инъекция → принуждение
контрактом гейта); без третьего знание остаётся необязательным. Выбор метода
и его механика сюда не входят — это делает method_router.py кодом
(без LLM) + prompts/contracts/*.md (инжектится отдельно, см. main() в
update_scenarios.py).

Вердикты из knowledge/sources/ НЕ подгружаются: они датированы и якорят анализ
на чужое мнение месячной давности. Используются точечно, руками.
"""
from __future__ import annotations

import email
import email.policy
import pathlib
import re
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"
# None means a sibling of the active KNOWLEDGE_DIR. Besides keeping binaries
# outside knowledge/, this makes callers which isolate KNOWLEDGE_DIR in a
# temporary directory isolate originals as well.
ORIGINALS_DIR: Path | None = None
THESIS_DIR = Path(__file__).parent.parent / "thesis"

# Предустановленные намеренные якоря. Чтобы подключить следующий тезис,
# достаточно добавить одну строку; файл остаётся на месте и не копируется в
# sources/. Регистр ключа нормализуется на входе публичных функций.
THESIS_FILES = {
    "NBIS": "clickhouse_thesis.md",
}

# Символы, не байты: старый порог в байтах (8KB ≈ 5500 символов русского
# текста при UTF-8 ~1.5 байт/символ) резал весь thesis/ до оглавления —
# ClickHouse-тезис (15K симв.) для NBIS доезжал без доли 0.30 и оценки
# $15-25B (#39 A/B, 20.08). Порог с полуторным запасом к самому большому
# файлу в thesis/ (burry_archive.md, 30.5K симв.) — сегодня ничего не режется.
# max_tokens этим лимитом не становится (D22): считаем в токенах модель сама,
# порог здесь — просто чтобы не заливать промпт нечитаным.
MAX_SOURCE_CHARS = 40_000


@dataclass(frozen=True)
class HumanSource:
    name: str
    date: str
    human_provided: bool
    source_file: str
    text: str
    path: Path
    preset: bool = False
    image_count: int = 0
    candidate_image_count: int = 0
    original_path: Path | None = None


@dataclass(frozen=True)
class MhtmlImage:
    content_location: str
    content_type: str
    content: bytes


def _originals_dir() -> Path:
    return ORIGINALS_DIR or KNOWLEDGE_DIR.parent / "human-source-originals"

# Общие для любой бумаги — только тиринг. methods/_choosing.md и
# метод-специфичные файлы (sotp.md/ev_revenue.md) сюда НЕ идут: метод теперь
# выбирает method_router.py кодом, а механику несёт сам контракт
# (prompts/contracts/*.md, инжектится в промпт напрямую) — дублировать её
# ещё и вики-файлом значит вернуть вес, который редизайн 30.07 убирал.
# patterns/red_flags.md тоже убран: формулы (∆E, гудвилл%, TBV) теперь считает
# _fetch_financials кодом и кладёт готовый результат в блок данных — модели
# больше не нужна инструкция «как считать», только «прокомментируй» (в
# task_scenarios_base.md, Step 1).
# aict_tiers_brief.md — сжатая версия aict_tiers.md (таблица + правило выбора,
# без развёрнутых обоснований и таблицы примеров) — полная версия для
# человека остаётся в knowledge/classifiers/aict_tiers.md.
CORE_FILES = [
    "classifiers/aict_tiers_brief.md",
    # Каталог призм: признаки стока → применимые книги → метод/cross-check.
    # Шаг 0 аналитика (task_scenarios_base.md) выбирает призму по нему.
    "classifiers/book_prisms.md",
]


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Единственная точка парсинга frontmatter: (метаданные, тело)."""
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n?", text, re.DOTALL)
    if not match:
        return {}, text
    result = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip('"\'')
    return result, text[match.end():]


def _strip_frontmatter(text: str) -> str:
    return _frontmatter(text)[1]


def _ticker(value: str) -> str:
    ticker = str(value or "").strip().upper()
    if not ticker or not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]*", ticker):
        raise ValueError("некорректный тикер")
    return ticker


def _slug(filename: str) -> str:
    stem = Path(filename).stem.lower()
    stem = re.sub(r"[^\w]+", "-", stem, flags=re.UNICODE).strip("-_")
    return stem or "source"


def _mhtml_to_text(raw: bytes) -> str:
    """Достать читаемый текст из сохранённой страницы (.mhtml/.mht).

    MHTML — MIME-архив: HTML документа лежит частью multipart/related рядом с
    картинками и стилями, обычно в quoted-printable. Разбирает stdlib email,
    текст из HTML достаёт bs4 (обе уже в окружении: bs4 приходит с yfinance).

    Первая строка результата — исходный URL страницы: human-source без адреса
    невозможно перепроверить, а для сохранённой веб-страницы это единственное
    место, где адрес вообще есть.
    """
    from bs4 import BeautifulSoup

    msg = email.message_from_bytes(raw, policy=email.policy.default)
    html, charset = None, None
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            # Байты, а не get_content(): Chrome обычно не пишет charset в
            # заголовок части, и email молча берёт us-ascii — кириллица и
            # типографские апострофы превращались в «������». Кодировку
            # определяет bs4 (UnicodeDammit), он читает и <meta charset> внутри
            # самого документа.
            html = part.get_payload(decode=True)
            charset = part.get_content_charset()
            break
    if not html:
        raise ValueError("в архиве нет HTML-части — это не сохранённая страница")

    soup = BeautifulSoup(html, "lxml", from_encoding=charset)
    # Обвязка страницы (меню, «Подарите подписку», счётчики) — чистый шум в
    # промпте: 1-11% объёма на проверенных Substack/alenka-архивах, тело статьи
    # не задевается.
    for tag in soup(["script", "style", "noscript", "nav", "header", "footer",
                     "aside", "form", "button"]):
        tag.decompose()
    text = soup.get_text("\n")
    # Разметка страницы даёт десятки пустых строк подряд — схлопываем, иначе
    # лимит MAX_SOURCE_CHARS уходит на пустоту.
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    lines = [line.strip() for line in text.splitlines()]
    body = "\n".join(lines).strip()
    if not body:
        raise ValueError("страница не содержит текста")

    url = msg.get("Snapshot-Content-Location") or msg.get("Content-Location") or ""
    title = str(msg.get("Subject") or "").strip()
    head = []
    if title:
        head.append(f"# {title}")
    if url:
        head.append(f"Источник: {url}")
    return "\n\n".join(head + [body]) if head else body


def _mhtml_images(raw: bytes) -> list[MhtmlImage]:
    """Extract every MIME image without trying to judge its usefulness."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    result = []
    for part in msg.walk():
        if part.get_content_maintype() != "image":
            continue
        result.append(MhtmlImage(
            content_location=str(part.get("Content-Location") or "").strip(),
            content_type=part.get_content_type(),
            content=part.get_payload(decode=True) or b"",
        ))
    return result


def _location_prefixes(location: str) -> list[str]:
    """Return useful URL directory prefixes, deepest first."""
    parsed = urlsplit(location)
    if not parsed.netloc:
        return []
    segments = [segment for segment in parsed.path.split("/")[:-1] if segment]
    # A host root or a single generic directory (e.g. /images/) groups unrelated
    # page chrome too readily. Publication folders in real archives carry a
    # structured path (/YYYY/MM/DD/id/...).
    return [f"{parsed.scheme.lower()}://{parsed.netloc.lower()}/"
            + "/".join(segments[:depth]) + "/"
            for depth in range(len(segments), 1, -1)]


def _publication_id(page_location: str) -> str | None:
    """Trailing digit run of the page URL's own last path segment.

    Confirmed on a real archive (alenka.capital, 21.08): the page lives under
    /post/<slug>_120446/ while its media lives under
    /data/uploads/2026/08/11/120446/ — no directory prefix in common at all,
    only this id repeated as a literal path segment. Prefix matching alone
    finds nothing there and falls through to the repeated-folder heuristic
    below, which then picked a site-wide emoji folder (81 images) over the
    24 real article images.

    Scans only the page's own last segment, not every image path: an earlier
    version matched any 4+ digit run anywhere, which also caught the "2026"/
    "2027" in dated upload folders — cross-contaminating with a DIFFERENT
    article's images (120027, 120322) that merely shared a year. A post id is
    the tail of the page's own slug, not any number seen anywhere.
    """
    segments = [s for s in urlsplit(page_location).path.split("/") if s]
    if not segments:
        return None
    match = re.search(r"(\d{4,})$", segments[-1])
    return match.group(1) if match else None


def _mhtml_image_candidates(raw: bytes) -> list[MhtmlImage]:
    """Select the image group belonging to one publication URL folder.

    Chrome preserves the source URL as Content-Location. Article assets share
    its directory prefix; avatars, trackers and adverts live elsewhere. If a
    publisher separates HTML and media hosts, matching falls back first to a
    shared numeric post id, then to the largest repeated image group (deepest
    prefix breaking ties). File size never participates at any stage.
    """
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    images = _mhtml_images(raw)
    page_location = str(msg.get("Snapshot-Content-Location") or "").strip()
    if not page_location:
        page_location = next((
            str(part.get("Content-Location") or "").strip()
            for part in msg.walk() if part.get_content_type() == "text/html"
        ), "")
    page_prefixes = _location_prefixes(page_location)
    if page_prefixes:
        publication_prefix = page_prefixes[0]
        publication_images = [item for item in images
                              if publication_prefix in
                              _location_prefixes(item.content_location)]
        if publication_images:
            return publication_images

    publication_id = _publication_id(page_location)
    if publication_id:
        id_segments = {"/" + publication_id + "/", "_" + publication_id + "/"}
        id_images = [item for item in images
                    if any(marker in item.content_location
                           for marker in id_segments)]
        if id_images:
            return id_images

    # Some publishers keep the page and its media on different hosts, sharing
    # neither a prefix nor an id. Fall back to the largest repeated deep
    # folder among the images.
    groups: dict[str, set[int]] = {}
    for index, item in enumerate(images):
        for prefix in _location_prefixes(item.content_location):
            groups.setdefault(prefix, set()).add(index)
    repeated = [(indexes, prefix) for prefix, indexes in groups.items()
                if len(indexes) >= 2]
    if not repeated:
        return []
    indexes, _ = max(repeated, key=lambda pair: (
        len(pair[0]), pair[1].count("/"), len(pair[1])))
    return [item for index, item in enumerate(images) if index in indexes]


def save_human_source(ticker: str, source_file: str, content: bytes | str,
                      *, source_date: dt.date | None = None) -> Path:
    """Сохранить пользовательский md/txt/mhtml как намеренный per-ticker якорь.

    Сохранённая страница (.mhtml/.mht) конвертируется в текст на входе, а не при
    чтении: в sources/ лежат только markdown-файлы, и всё, что читает якоря
    (_source_from_path, format_human_sources), остаётся без изменений.
    """
    suffix = Path(source_file or "").suffix.lower()
    if suffix not in {".md", ".txt", ".mhtml", ".mht"}:
        raise ValueError("поддерживаются только файлы .md, .txt и .mhtml")
    raw = None
    image_count = candidate_image_count = 0
    if suffix in {".mhtml", ".mht"}:
        raw = content if isinstance(content, bytes) else str(content).encode("utf-8")
        body = _mhtml_to_text(raw)
        images = _mhtml_images(raw)
        image_count = len(images)
        candidate_image_count = len(_mhtml_image_candidates(raw))
    elif isinstance(content, bytes):
        try:
            body = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("файл должен быть в UTF-8") from exc
    else:
        body = str(content)
    if not body.strip():
        raise ValueError("нельзя сохранить пустой human-source")
    day = source_date or dt.date.today()
    target_dir = KNOWLEDGE_DIR / "sources" / _ticker(ticker)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{day.isoformat()}-{_slug(source_file)}.md"
    original_relative = ""
    if raw is not None:
        original_dir = _originals_dir() / _ticker(ticker)
        original_dir.mkdir(parents=True, exist_ok=True)
        original = original_dir / f"{target.stem}{suffix}"
        original.write_bytes(raw)
        original_relative = f"{_ticker(ticker)}/{original.name}"
    safe_name = str(source_file).replace("\\", "/").rsplit("/", 1)[-1]
    quoted_name = safe_name.replace('"', '\\"')
    metadata = (
        "---\n"
        "human_provided: true\n"
        f'source_file: "{quoted_name}"\n'
        f"date: {day.isoformat()}\n")
    if raw is not None:
        metadata += (
            f"image_count: {image_count}\n"
            f"candidate_image_count: {candidate_image_count}\n"
            f'original_file: "{original_relative}"\n')
    target.write_text(metadata + "---\n\n" + body.strip() + "\n",
                      encoding="utf-8")
    return target


def _source_from_path(path: Path, *, preset: bool = False) -> HumanSource | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    meta, body_raw = _frontmatter(raw)
    # knowledge/sources также содержит датированные web-вердикты. Они не
    # становятся якорями лишь потому, что лежат рядом: допускаются только
    # файлы, намеренно помеченные при загрузке человеком.
    if not preset and meta.get("human_provided", "").lower() != "true":
        return None
    body = body_raw.strip()
    if not body:
        return None
    source_file = meta.get("source_file") or path.name
    source_date = meta.get("date") or (
        "—" if preset else dt.date.fromtimestamp(path.stat().st_mtime).isoformat())
    original_path = None
    original_file = meta.get("original_file", "")
    if original_file and not preset:
        originals_dir = _originals_dir()
        candidate = originals_dir / pathlib.PurePosixPath(original_file)
        try:
            if candidate.resolve().is_relative_to(originals_dir.resolve()):
                original_path = candidate
        except (OSError, ValueError):
            pass
    try:
        image_count = int(meta.get("image_count", "0"))
        candidate_image_count = int(meta.get("candidate_image_count", "0"))
    except ValueError:
        image_count = candidate_image_count = 0
    return HumanSource(
        name=path.name, date=source_date, human_provided=True,
        source_file=source_file, text=body, path=path, preset=preset,
        image_count=image_count, candidate_image_count=candidate_image_count,
        original_path=original_path)


def load_human_sources(ticker: str) -> list[HumanSource]:
    """Прочитать только источники заданной бумаги, включая thesis-реестр."""
    key = _ticker(ticker)
    result = []
    source_dir = KNOWLEDGE_DIR / "sources" / key
    if source_dir.is_dir():
        for path in sorted(source_dir.iterdir()):
            if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
                source = _source_from_path(path)
                if source:
                    result.append(source)
    thesis_file = THESIS_FILES.get(key)
    if thesis_file:
        source = _source_from_path(THESIS_DIR / thesis_file, preset=True)
        if source:
            result.append(source)
    return result


def _heading_summary(text: str) -> str:
    headings = [m.group(1).strip() for m in re.finditer(
        r"(?m)^#{1,6}\s+(.+?)\s*$", text)]
    if headings:
        return "Заголовки: " + " · ".join(headings[:20])
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return "Выжимка: " + first[:500]


def delete_human_source(ticker: str, source_file: str) -> bool:
    """Удалить материал человека по имени файла в knowledge/sources/<TICKER>.

    Preset-тезисы (thesis/) не удаляются: они не копия и не загрузка человека —
    файл живёт своей жизнью в репозитории, реестр THESIS_FILES лишь ссылается
    на него. Удалять чужой файл из-под кнопки «убрать материал» неверно.
    """
    source_dir = KNOWLEDGE_DIR / "sources" / _ticker(ticker)
    if not source_dir.is_dir():
        return False
    target = source_dir / pathlib.PurePath(str(source_file)).name
    if target.parent.resolve() != source_dir.resolve() or not target.is_file():
        return False
    source = _source_from_path(target)
    if source and source.original_path and source.original_path.is_file():
        source.original_path.unlink()
    target.unlink()
    return True


def human_source_labels(ticker: str) -> list[str]:
    """Короткие подписи материалов: чем прогон подкреплён, без их текста.

    Нужны там, где важен факт и происхождение, а не содержание: манифест в
    Спеке (человек утверждает её до прогона и должен видеть, учтён ли файл) и
    пометка у записи в истории оценок.
    """
    return [f"{source.source_file} ({source.date})"
            + (" · preset" if source.preset else "")
            for source in load_human_sources(ticker)]


def format_source_digests(ticker: str, selected_source_names) -> tuple[list[str], str]:
    """Format latest digests of an explicit human selection for a RunSpec.

    ``source_name`` is the stored file name (``HumanSource.path.name``), not
    the display label.  A stale/invalid selection is an error: silently
    omitting it would make the approved manifest differ from the prompt.
    """
    from digest import latest_source_digest

    requested = list(dict.fromkeys(selected_source_names or ()))
    sources = {source.path.name: source for source in load_human_sources(ticker)}
    labels = []
    chunks = []
    for source_name in requested:
        source = sources.get(source_name)
        if source is None:
            raise ValueError(f"Материал не найден: {source_name}")
        digest_row = latest_source_digest(ticker, source_name)
        if digest_row is None:
            raise ValueError(
                f"Для материала {source.source_file} нет выжимки — сначала разберите материал")
        label = (f"{source.source_file} ({source.date})"
                 + (" · preset" if source.preset else ""))
        labels.append(label)
        chunks.append(f"### {label}\n{digest_row['digest_text'].strip()}")
    if not chunks:
        return labels, ""
    return labels, "=== Выжимки материалов человека ===\n" + "\n\n".join(chunks)


def format_human_sources(ticker: str) -> str:
    """Секция для Спеки/research: полный файл до MAX_SOURCE_CHARS, иначе заголовки.

    Обрезка помечается прямо в тексте — Денис одобряет Спеку до прогона (D13),
    и молчаливый переход на оглавление выглядел бы как полноценный источник.
    """
    chunks = []
    for source in load_human_sources(ticker):
        truncated = len(source.text) > MAX_SOURCE_CHARS
        body = (source.text if not truncated else
                f"⚠️ источник обрезан ({len(source.text)} симв. > лимита "
                f"{MAX_SOURCE_CHARS}) — ниже только заголовки, фактов может "
                f"не хватать\n{_heading_summary(source.text)}")
        flag = "human_provided=true"
        if source.preset:
            flag += ", preset thesis"
        chunks.append(
            f"### {source.source_file} · {source.date} · {flag}\n{body}")
    if not chunks:
        return ""
    return "=== Материалы человека (первичные источники) ===\n" + "\n\n".join(chunks)


def _read(rel_path: str) -> str | None:
    p = KNOWLEDGE_DIR / rel_path
    if not p.exists():
        return None
    try:
        return _strip_frontmatter(p.read_text(encoding="utf-8")).strip()
    except Exception:
        return None


def load_for(research_type: str = "default") -> str:
    """Собрать блок знаний под тип бумаги. Пустая строка, если базы нет —
    пайплайн должен работать и без неё (деградация, а не падение).

    research_type пока не влияет на набор файлов (CORE_FILES одинаковы для
    всех) — параметр оставлен для шага 2.3 (сжатые тиры/флаги по типу бизнеса)."""
    if not KNOWLEDGE_DIR.exists():
        return ""

    chunks = []
    for rel in CORE_FILES:
        body = _read(rel)
        if body:
            chunks.append(f"<!-- knowledge/{rel} -->\n{body}")

    if not chunks:
        return ""
    return (
        "\n\n=== БАЗА ЗНАНИЙ (knowledge/) ===\n"
        "Это рабочие процедуры, не справочный материал. Дерево выбора метода "
        "и чеклист красных флагов — обязательны к применению.\n\n"
        + "\n\n---\n\n".join(chunks)
    )


def prism_catalog_books() -> list[str]:
    """Вернуть имена книг из каталога, сохраняя порядок и не дублируя их.

    Каталог — единственный источник списка. Каждая запись — жирная строка
    ``**Название — Автор**`` (сама по себе, не заголовок раздела), за которой
    следует описание (маркеры «Признаки/Предлагает/Не применим»). Заголовки
    (``##``) — это разделы каталога (Основные / Дополняют стиль / Процедура),
    не книги. Пустой/недоступный каталог даёт пустой список, а не хардкод.
    """
    body = _read("classifiers/book_prisms.md")
    if not body:
        return []
    books = []
    for match in re.finditer(r"(?m)^\*\*(.+?)\*\*\s*$", body):
        name = re.sub(r"^\d+[.)]\s*", "", match.group(1)).strip()
        name = re.sub(r"\s+[—–]\s+.*$", "", name).strip(" `*_\t")
        if name and name not in books:
            books.append(name)
    return books
