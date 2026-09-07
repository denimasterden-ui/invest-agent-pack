"""Разбор материала по трём адресатам — слой 0→2 v2 (SPC-008, решение 15).

Материал от человека или стороннего аналитика несёт разное: знание о самом
бизнесе (сопоставимые, физические драйверы, ограничители, устойчивые
характеристики), числа, которыми можно сверить допущения базовой линии, и
утверждения о будущем. Раньше он уходил целиком в оценку — знание о бизнесе
исчезало вместе с материалом, а сверить базовую линию было нечем. Прототип
24.08 поймал на этом второе: автоисточник отдавал баланс за прошлый год при
доступном квартальном, материал это замечал, и расхождение двух мер базовой
линии падало вдвое.

Один разбор, три адресата:
  profile_updates     — дополняют слой 0 и остаются между прогонами
                        (profile.record_change, автор события «material»);
  baseline_checks     — сверка допущений слоя 1 с числами материала, по вердикту
                        на каждое число: подтверждает, противоречит, уточняет;
  thesis_candidates   — сырьё для слоя 2 (форков); здесь только собирается,
                        хранилища у тезисов в этом срезе нет.

Якорь — первоисточник, и это проверяет код, а не текст роли: материал есть
НОСИТЕЛЬ знания, а не источник числа. Если он цитирует отчётность компании,
источником считается компания, а материал остаётся путём, которым число пришло
(поле ``via`` у дополнений — его ставит слой, а не модель). Чужой разбор,
названный источником собственного числа, — та же подмена класса якоря, что и
суждение, выданное за company_guide.

Отказ разбора не пишет ничего, и это одна причина на весь разбор: материал,
чей вердикт расходится с арифметикой, ненадёжный источник и о бизнесе тоже —
знание о нём не должно попадать в профиль, потому что переживёт прогоны.

Слой сам в сеть не ходит и провайдера не выбирает (решение 17): живой вызов —
одна функция (``call_model``), идущая тем же CLI, что и остальные роли на линии.
Ответ модели разбирается здесь и только здесь; структурные проверки живут в
коде, потому что правки промпта не чинят арифметику и якоря.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from . import profile, profile_store
from .kernel import store_client

ROOT = Path(__file__).resolve().parent.parent

# ── словари, по которым слой проверяет ответ модели ──────────────────────────

# Классы якорей — та же лексика, на которой строится слой 2 (форки).
COMPANY_GUIDE = "company_guide"      # гайд, заявление или отчётность эмитента
MACRO_GUIDANCE = "macro_guidance"    # ВВП, ставка ЦБ, гайденс регулятора
PEER_STATS = "peer_stats"            # статистика конкурентов / peer-группы
OWN_HISTORY = "own_history"          # собственный мультипликатор бумаги
ANALYST_JUDGEMENT = "analyst_judgement"  # источника нет
ANCHOR_CLASSES = (COMPANY_GUIDE, MACRO_GUIDANCE, PEER_STATS, OWN_HISTORY,
                  ANALYST_JUDGEMENT)
ANCHORED = tuple(c for c in ANCHOR_CLASSES if c != ANALYST_JUDGEMENT)

# Вердикт проверки базовой линии — результат разбора, а не украшение. Имена
# с префиксом VERDICT_, чтобы не столкнуться с confirms слоя 2 (number |
# driver | estimate) — это разные словари о разных вещах.
VERDICT_CONFIRMS = "подтверждает"
VERDICT_CONTRADICTS = "противоречит"
VERDICT_REFINES = "уточняет"
VERDICTS = (VERDICT_CONFIRMS, VERDICT_CONTRADICTS, VERDICT_REFINES)

# Какие числа допущений материал может сверять. Ключ фиксирован, чтобы код мог
# сравнить число материала с числом базовой линии, а не поверить формулировке.
DPS = "dps"          # дивиденд на акцию
EPS = "eps"          # прибыль на акцию за 12 месяцев
ROE = "roe"          # рентабельность капитала, в долях
BVPS = "bvps"        # капитал на акцию
PBV = "pbv"          # мультипликатор капитала
CHECK_KEYS = (DPS, EPS, ROE, BVPS, PBV)
_CURRENCY_KEYS = (DPS, EPS, BVPS)   # остальные безразмерны

# Метки направлений тезисов — те же, что у сценариев: четвёртой нет.
BULL, BASE, BEAR = "bull", "base", "bear"
DIRECTIONS = (BULL, BASE, BEAR)

# Три адресата разбора — ключи ответа модели и поля Split. Один список, потому
# что он обязан совпадать в промпте, в разборе и в выводе команды.
PARTS = ("profile_updates", "baseline_checks", "thesis_candidates")

# Числа материала и допущения считаются согласованными, пока расходятся меньше
# чем на это. Порог — суждение; объявлен, чтобы его можно было спорить, а не
# прятать в сравнении. Округление и курс дают доли процента, подмена величины —
# десятки.
CHECK_TOLERANCE = 0.10

# Слова, которыми модель называет материал источником числа. Материал — путь,
# которым число пришло: источником он быть не может, как его ни назови.
CARRIER_WORDS = ("выжимка", "материал", "разбор аналитика")

# Разбор — работа аналитика, реальный ответ подписки идёт минутами: тот же
# запас, что у выжимки (digest.py), а не дефолтные 90с research-брифа.


@dataclass(frozen=True)
class Material:
    """Что пришло на разбор: текст и его имя — то, чем число пришло, не источник."""
    name: str
    text: str


@dataclass(frozen=True)
class Problem:
    """Одно нарушение формы разбора: где сломано и что не так."""
    code: str        # answer | part | field | anchor | verdict | unit | value | thesis
    message: str


@dataclass(frozen=True)
class ProfileUpdate:
    """Дополнение профиля: знание о бизнесе, которое переживёт прогоны."""
    field: str
    value: object
    reason: str
    anchor_class: str
    source: str          # первоисточник, не материал
    via: str = ""        # материал, которым число пришло — ставит слой


@dataclass(frozen=True)
class BaselineCheck:
    """Одно число против допущения базовой линии, с вердиктом по нему.

    Путь, которым число пришло, у разбора общий (Split.material_name): своей
    ссылки на материал проверке не нужно.
    """
    key: str
    what: str
    value: float                 # как сказано в материале, с его валютой
    unit: str
    verdict: str
    note: str
    anchor_class: str
    source: str                  # первоисточник, не материал
    value_financial: float | None = None   # то же число в валюте отчётности
    baseline_value: float | None = None    # допущение, с которым сверили
    delta: float | None = None             # расхождение, долей


@dataclass(frozen=True)
class ThesisCandidate:
    """Утверждение о будущем: направление и условия, проверяемые отчётом."""
    direction: str
    thesis: str
    must_be_true: tuple
    drivers_touched: tuple = ()


@dataclass(frozen=True)
class Split:
    """Разбор одного материала. Отказ — когда форма сломана, писать тогда нечего."""
    ticker: str
    material_name: str
    profile_updates: tuple = ()
    baseline_checks: tuple = ()
    thesis_candidates: tuple = ()
    problems: tuple = ()
    empty_parts: tuple = ()

    def is_refusal(self):
        return bool(self.problems)


# ── разбор ответа модели ─────────────────────────────────────────────────────

def parse(answer, ticker, material_name):
    """Ответ модели → разбор по трём адресатам. Форму проверяет код.

    Отказ собирается из всех нарушений сразу, а не по первому: у отчётности
    ходят один раз, и причины сразу видны целиком. Части при отказе пусты —
    из сломанного разбора ничего не пишется.
    """
    data, problems = _json_answer(answer)
    if data is None:
        return Split(ticker=ticker, material_name=material_name,
                     problems=tuple(problems))
    updates, problems = _updates(data, ticker, material_name, problems)
    checks, problems = _checks(data, ticker, material_name, problems)
    theses, problems = _theses(data, problems)
    items = dict(zip(PARTS, (updates, checks, theses)))
    empty = tuple(name for name in PARTS if not items[name])
    return Split(ticker=ticker, material_name=material_name,
                 profile_updates=updates, baseline_checks=checks,
                 thesis_candidates=theses, problems=tuple(problems),
                 empty_parts=empty)


def _json_answer(answer):
    """JSON из ответа: fenced-блок, иначе внешние скобки. Не JSON — отказ."""
    text = (answer or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        loose = re.search(r"\{.*\}", text, re.S)
        raw = loose.group(0) if loose else None
    if raw is None:
        return None, [Problem(
            "answer", "в ответе нет JSON — разбор нечего раскладывать по частям")]
    try:
        data = json.loads(raw)
    except ValueError as e:
        return None, [Problem("answer", f"ответ не читается как JSON ({e})")]
    if not isinstance(data, dict):
        return None, [Problem("answer", "ответ ждёт объект с тремя частями")]
    missing = [part for part in PARTS if part not in data]
    if missing:
        return None, [Problem(
            "part", f"в ответе нет части {', '.join(missing)} — разбор всегда "
                    f"несёт все три, даже когда материал ничего не добавляет")]
    return data, []


def _items(data, part, problems):
    """Список одной части, уже почищенный от не-объектов.

    Форма общая у всех трёх частей, поэтому и скелет один: различаются только
    проверки полей и их сообщения.
    """
    raw = data.get(part) or []
    if not isinstance(raw, list):
        problems.append(Problem(_code_of(part), f"{part} ждёт список, не объект"))
        return []
    named = {"profile_updates": "дополнение", "baseline_checks": "проверка",
             "thesis_candidates": "тезис"}
    clean = []
    for item in raw:
        if isinstance(item, dict):
            clean.append(item)
        else:
            problems.append(Problem(_code_of(part),
                                    f"{named[part]} не объект: {item!r}"))
    return clean


def _code_of(part):
    """Код отказа по части: он же указывает, какое поле надо чинить."""
    return {"profile_updates": "field", "baseline_checks": "verdict",
            "thesis_candidates": "thesis"}[part]


def _updates(data, ticker, material_name, problems):
    out = []
    for item in _items(data, "profile_updates", problems):
        field, value = item.get("field"), item.get("value")
        anchor, source = item.get("anchor_class") or "", item.get("source") or ""
        if field not in profile_store.OVERRIDABLE:
            problems.append(Problem(
                "field", f"{field!r}: не поле профиля — дополнить можно "
                         f"{', '.join(profile_store.OVERRIDABLE)}"))
            continue
        if not _shape_ok(field, value):
            problems.append(Problem(
                "field", f"{field}: значение не той формы — {value!r}"))
            continue
        problem = _anchor(anchor, source, material_name, ticker)
        if problem:
            problems.append(problem)
            continue
        if not (item.get("reason") or "").strip():
            problems.append(Problem(
                "field", f"{field}: дополнение без причины не пишется — история "
                         f"профиля обязана отвечать, почему стало так"))
            continue
        out.append(ProfileUpdate(field=field, value=value,
                                 reason=item["reason"].strip(),
                                 anchor_class=anchor, source=source.strip(),
                                 via=material_name))
    return tuple(out), problems


def _checks(data, ticker, material_name, problems):
    out = []
    for item in _items(data, "baseline_checks", problems):
        key, value = item.get("key") or "", item.get("value")
        verdict = item.get("verdict") or ""
        anchor, source = item.get("anchor_class") or "", item.get("source") or ""
        if key not in CHECK_KEYS:
            problems.append(Problem(
                "key", f"{key!r}: не величина допущений — сверять можно "
                       f"{', '.join(CHECK_KEYS)}"))
            continue
        if not _is_number(value) or value <= 0:
            problems.append(Problem(
                "value", f"{key}: ждёт положительное число, пришло {value!r}"))
            continue
        if verdict not in VERDICTS:
            problems.append(Problem(
                "verdict", f"{key}: вердикт {verdict!r} — а он и есть результат, "
                           f"ждёт {' | '.join(VERDICTS)}"))
            continue
        if verdict != VERDICT_CONFIRMS and not (item.get("note") or "").strip():
            problems.append(Problem(
                "verdict", f"{key}: «{verdict}» без объяснения не читается — "
                           f"назови, чем именно число расходится с допущением"))
            continue
        unit = item.get("unit") or ""
        if key in _CURRENCY_KEYS and not unit.strip():
            problems.append(Problem(
                "unit", f"{key}: число без валюты — сравнить его с допущением "
                        f"нельзя"))
            continue
        problem = _anchor(anchor, source, material_name, ticker)
        if problem:
            problems.append(problem)
            continue
        out.append(BaselineCheck(
            key=key, what=(item.get("what") or "").strip(), value=float(value),
            unit=unit.strip(), verdict=verdict,
            note=(item.get("note") or "").strip(), anchor_class=anchor,
            source=source.strip()))
    return tuple(out), problems


def _theses(data, problems):
    out = []
    for item in _items(data, "thesis_candidates", problems):
        direction = item.get("direction") or ""
        thesis = (item.get("thesis") or "").strip()
        conditions = item.get("must_be_true") or []
        if direction not in DIRECTIONS:
            problems.append(Problem(
                "thesis", f"направление {direction!r} — ждёт "
                          f"{' | '.join(DIRECTIONS)}: четвёртой метки нет"))
            continue
        if not thesis:
            problems.append(Problem("thesis", "тезис пуст"))
            continue
        if not (isinstance(conditions, list) and conditions
                and all(isinstance(c, str) and c.strip() for c in conditions)):
            problems.append(Problem(
                "thesis", f"у тезиса «{thesis[:40]}» нет условий — без проверяемого "
                          f"условия это настроение, а не тезис"))
            continue
        touched = item.get("drivers_touched") or []
        out.append(ThesisCandidate(
            direction=direction, thesis=thesis,
            must_be_true=tuple(c.strip() for c in conditions),
            drivers_touched=tuple(t.strip() for t in touched if str(t).strip())))
    return tuple(out), problems


def _anchor(anchor_class, source, material_name, ticker=""):
    """Якорь на первоисточник. Материал источником числа не становится."""
    if anchor_class not in ANCHOR_CLASSES:
        return Problem("anchor", f"класс якоря {anchor_class!r} неизвестен — "
                                 f"{' | '.join(ANCHOR_CLASSES)}")
    if anchor_class == ANALYST_JUDGEMENT:
        if (source or "").strip() and _names_only_issuer(source, ticker):
            return _issuer_anchor_problem(anchor_class, source)
        return None
    if not (source or "").strip():
        return Problem("anchor", f"якорь {anchor_class} требует первоисточник — "
                                 f"чей это источник, а не откуда взято")
    source = source.strip()
    if source == (material_name or "").strip() or _names_carrier(source):
        return Problem("anchor", f"источником назван материал («{source}») — "
                                 f"материал носитель знания, а не источник числа; "
                                 f"назови компанию, регулятора или peer-группу")
    if anchor_class != COMPANY_GUIDE and _names_only_issuer(source, ticker):
        return _issuer_anchor_problem(anchor_class, source)
    return None


def _issuer_anchor_problem(anchor_class, source):
    return Problem(
        "anchor", f"источник назван эмитентом («{source}»), поэтому класс "
        f"якоря должен быть {COMPANY_GUIDE}, не {anchor_class}")


def _names_carrier(source):
    return any(word in source.lower() for word in CARRIER_WORDS)


def _names_issuer(source, ticker):
    """Whether source explicitly points to the issuer rather than market data."""
    lowered = source.lower()
    issuer_words = ("эмитент", "компани", "issuer", "annual report",
                    "quarterly report", "earnings release", "investor relations")
    if any(word in lowered for word in issuer_words):
        return True
    symbol = (ticker or "").lower()
    return bool(symbol and symbol in lowered)


def _names_only_issuer(source, ticker):
    """Whether every explicitly listed source points to the issuer."""
    components = re.split(r"\s*(?:\+|;|\||\n)\s*", source.strip())
    return bool(components) and all(
        component and _names_issuer(component, ticker)
        for component in components
    )


def _shape_ok(field, value):
    """Форма значения — как у журнала профиля, иначе реплей уронит сборку."""
    if field in profile_store.LIST_FIELDS:
        return (isinstance(value, list) and value
                and all(isinstance(v, str) and v.strip() for v in value))
    return isinstance(value, str) and value.strip()


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ── сверка с базовой линией ──────────────────────────────────────────────────

def compare(split, base):
    """Числа материала против допущений базовой линии: база, курс и расхождение.

    Сверяет КОД, а не верит формулировке: «подтверждает» при расхождении в
    десятки процентов — та же подмена, что якорь повыше классом. Базы нет
    (слой базовой линии отказал) — сравнить не с чем, вердикты остаются как
    объявлено: разбор от этого не ломается, сверка просто не состоялась.
    """
    if base.detail is None:
        return split
    out, problems = [], []
    for c in split.baseline_checks:
        value_fin, problem = _to_financial(c, base.detail)
        if problem is not None:
            problems.append(problem)
            out.append(c)
            continue
        base_value = _baseline_value(base.detail, c.key)
        delta = abs(value_fin - base_value) / base_value
        out.append(replace(c, value_financial=round(value_fin, 2),
                           baseline_value=round(base_value, 4),
                           delta=round(delta, 4)))
        problem = _verdict_problem(c, base_value, value_fin, delta)
        if problem is not None:
            problems.append(problem)
    return replace(split, baseline_checks=tuple(out),
                   problems=split.problems + tuple(problems))


def _to_financial(check, detail):
    """Число материала в валюте отчётности. Конвертацию делает код (D20)."""
    value = check.value
    if check.key in _CURRENCY_KEYS:
        if check.unit.lower() == detail.price_currency.lower():
            value = value / detail.fx
        elif check.unit.lower() != detail.financial_currency.lower():
            return None, Problem(
                "unit", f"{check.key}: число в «{check.unit}», а допущения в "
                        f"{detail.financial_currency} против цены в "
                        f"{detail.price_currency} — сравнить нельзя")
    elif check.key == ROE and check.unit.strip() in ("%", "процент"):
        value = value / 100
    return value, None


def _baseline_value(detail, key):
    """Допущение в валюте отчётности, собранное из публичных полей Detail слоя 1.

    Слой 1 здесь не дополняется: прибыль на акцию выводится из его же ROE и
    капитала на акцию, а не хранится отдельным полем. Курс делит, потому что
    Detail отдаёт деньги в валюте котировки, а материал сверяется с
    допущениями в валюте отчётности.
    """
    fx = detail.fx
    if key == DPS:
        return detail.dps / fx
    if key == BVPS:
        return detail.book_value_per_share / fx
    if key == EPS:
        return detail.roe * detail.book_value_per_share / fx
    if key == ROE:
        return detail.roe
    return detail.pbv_multiple


def _verdict_problem(check, base_value, value_fin, delta):
    """Вердикт против арифметики — отказ: вердикт и есть результат разбора.

    «Уточняет» арифметикой не проверяется вовсе: уточнение — это про то, ЧЕЙ
    период или чей капитал стоит за числом, а не про величину расхождения,
    поэтому законно и при расхождении в разы, и при почти полном сходстве.
    """
    if check.verdict == VERDICT_CONFIRMS and delta > CHECK_TOLERANCE:
        return Problem("verdict", f"{check.what or check.key}: материал "
                                  f"{format_number(value_fin)}, а допущение "
                                  f"{format_number(base_value)} — расходятся на "
                                  f"{delta:.0%}, «{VERDICT_CONFIRMS}» неверно")
    if check.verdict == VERDICT_CONTRADICTS and delta <= CHECK_TOLERANCE:
        return Problem("verdict", f"{check.what or check.key}: числа сходятся "
                                  f"(расхождение {delta:.1%}), "
                                  f"«{VERDICT_CONTRADICTS}» не к чему")
    return None


def format_number(value):
    """Число в сообщении и в выводе команды: деньги с десятыми, доли точнее.

    Без запятых в разрядах: они читаются как десятичные в десятичных долях
    валюты, которых здесь нет.
    """
    if abs(value) < 100:
        return f"{value:.2f}"
    return f"{value:,.1f}".replace(",", " ")


# ── запись ───────────────────────────────────────────────────────────────────

def apply(ticker, split):
    """Дополнения профиля — событиями с автором «материал». Отказ не пишется.

    Возвращает пары (поле, вступило в силу): смена меры через материал так же
    ждёт человека, как и смена меры от модели — чужой разбор не меняет меру,
    которой считается базовая линия.
    """
    if split.is_refusal():
        raise ValueError("разбор с отказами не пишется: "
                         + "; ".join(f"{p.code}: {p.message}"
                                     for p in split.problems))
    written = []
    for u in split.profile_updates:
        if store_client.configured():
            store_client.propose_profile_candidate(
                ticker, u.field, u.value, u.reason, u.anchor_class, u.source)
            applied = False
        else:
            applied = profile.record_change(ticker, u.field, u.value,
                                            reason=u.reason, author="material")
        written.append((u.field, applied))
    return tuple(written)


# ── промпт и живой вызов ─────────────────────────────────────────────────────

ROLE = """Разбери материал по трём адресатам. Материал написан человеком или
сторонним аналитиком — он НОСИТЕЛЬ знания, а не источник чисел. Если он цитирует
отчётность или заявление компании, источником считается компания, а материал
остаётся путём, которым число пришло.

1) profile_updates — что материал говорит о САМОМ БИЗНЕСЕ и его оценке. Поля,
   которые можно дополнить: comps, drivers, caps, multiple_metric, data_gaps,
   business_kind, measure_reason. Списковые поля (comps, drivers, caps,
   data_gaps) возвращай СПИСКОМ ЦЕЛИКОМ — известное показано ниже, и событие
   запишет ровно то, чем значение стало: стёртое известное будет потеряно.
   Это знание живёт между прогонами, поэтому сюда идёт только то, что не
   устареет через квартал.

2) baseline_checks — числа, которыми можно ПРОВЕРИТЬ допущения базовой линии:
   дивиденд, прибыль на акцию, ROE, капитал на акцию, мультипликатор.
   key — из словаря: dps, eps, roe, bvps, pbv. value — ПОЛОЖИТЕЛЬНОЕ число,
   unit — валюта числа (для roe и pbv — «доля» и «x»; ROE в долях: 0.38, не 38).
   verdict: подтверждает | противоречит | уточняет. «Противоречит» и
   «уточняет» требуют note: чем именно число расходится с допущением.

3) thesis_candidates — утверждения о БУДУЩЕМ: direction bull | base | bear,
   thesis, must_be_true — условия, проверяемые следующим отчётом,
   drivers_touched — какие драйверы профиля затронуты.

Не дублируй: число, проверяющее базовую линию, не повторяй в тезисах.
Якорь: anchor_class — company_guide | macro_guidance | peer_stats |
own_history | analyst_judgement. У всех, кроме analyst_judgement, обязателен
source, и это ПЕРВОИСТОЧНИК — компания, регулятор или peer-группа; материал,
его автор и слово «выжимка» источником быть не могут.

Верни ТОЛЬКО JSON:
{"profile_updates": [{"field": "comps", "value": ["AAA"],
   "reason": "...", "anchor_class": "peer_stats", "source": "..."}],
 "baseline_checks": [{"key": "dps", "what": "дивиденд на акцию",
   "value": 3994.8, "unit": "KZT", "verdict": "подтверждает",
   "note": "", "anchor_class": "company_guide", "source": "гайд компании"}],
 "thesis_candidates": [{"direction": "bull", "thesis": "...",
   "must_be_true": ["..."], "drivers_touched": ["..."]}]}
"""

RESEARCH_ROLE = """Ты собираешь материал для последующего разбора инвестиционным
слоем. Исследуй только перечисленные незаполненные поля профиля. Для каждого
поля дай конкретные найденные значения, единицы, период и первоисточник.
Отделяй факты от оценок; если надёжного значения нет, прямо так и напиши.
Верни обычный текстовый материал, не JSON: следующий слой сам разложит его по
profile_updates, baseline_checks и thesis_candidates."""


def build_research_prompt(prof):
    """Адресный запрос на каждый незаполненный data_gap effective-профиля."""
    gaps = tuple(prof.effective.data_gaps)
    if not gaps:
        raise ValueError(f"{prof.ticker}: в профиле нет data_gaps")
    gap_lines = "\n".join(f"{n}. {gap}" for n, gap in enumerate(gaps, 1))
    return "\n".join((
        RESEARCH_ROLE,
        "=== БУМАГА ===",
        f"{prof.ticker}, {prof.effective.business_kind}.",
        "=== КОНКРЕТНЫЕ ПРОБЕЛЫ ДАННЫХ ===",
        gap_lines,
        "Закрой каждый пункт отдельно. Не расширяй запрос до общего обзора "
        "компании и не подменяй отсутствующее число рассуждением.",
    ))


def build_prompt(prof, material, base=None):
    """Промпт разбора: термины профиля, допущения базовой линии и сам материал.

    Драйверы и сопоставимые показываются не для галочки: модель не должна
    выдумывать их заново там, где они уже известны, а сверять материал есть
    смысл только против тех допущений, которые объявлены. Базы нет — части с
    ней просто не появляется, разбор от этого не зависит.
    """
    eff = prof.effective
    parts = [
        ROLE,
        "=== БУМАГА ===",
        f"{prof.ticker}, {eff.business_kind}.",
        f"Мера базовой линии: {prof.measure} — {prof.measure_reason}.",
        f"Известные драйверы: {', '.join(eff.drivers) or 'нет'}.",
        f"Известные сопоставимые: {', '.join(eff.comps) or 'нет'} "
        f"(в единицах {eff.multiple_metric}).",
        f"Известные ограничители роста: {', '.join(eff.caps) or 'нет'}.",
        f"Пробел данных: {', '.join(eff.data_gaps) or 'нет'}.",
    ]
    if base is not None and base.detail is not None:
        d = base.detail
        low, high = base.corridor
        parts += [
            f"Базовая линия: {low:.2f} – {high:.2f} {base.price_currency} "
            f"при цене {base.price:.2f}; расхождение мер "
            f"{base.divergence:.1%}.",
            f"Допущения базовой линии: ставка {d.r:.0%} ({d.r_why}), "
            f"рост {d.g:.0%} ({d.g_why}), дивиденд {d.dps} "
            f"{base.price_currency}, ROE {d.roe:.1%}, капитал на акцию "
            f"{d.book_value_per_share} {base.price_currency}.",
        ]
    parts += [f"=== МАТЕРИАЛ: {material.name} ===", material.text]
    return "\n".join(parts)


def _material_db():
    """AGENT_MATERIAL_DB — изолированный источник материала для эвала."""
    override = os.environ.get("AGENT_MATERIAL_DB")
    return Path(override) if override else ROOT / "invest.db"


def latest_material(ticker, db_path=None):
    """Последняя выжимка материала тикера или None, если разбирать нечего.

    Чтение через read-only соединение: слой не имеет права менять боевую БД,
    и нарушение не зависит от аккуратности запроса. База без таблицы или без
    файла — «материала нет», а не сбой.
    """
    path = Path(db_path) if db_path else _material_db()
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            row = con.execute(
                "select source_name, digest_text from source_digests "
                "where ticker=? order by id desc limit 1", (ticker,)).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    return Material(name=row[0], text=row[1]) if row else None


def material_from_file(path):
    """Материал из файла команды. Пустой или нечитаемый — ошибка вызывающего."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"{path}: материал не читается ({e})") from e
    if not text.strip():
        raise ValueError(f"{path}: материал пуст — разбирать нечего")
    return Material(name=Path(path).name, text=text)
