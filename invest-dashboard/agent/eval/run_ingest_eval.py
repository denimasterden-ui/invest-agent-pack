#!/usr/bin/env python3
"""Офлайн-регрессия разбора материала по трём адресатам (SPC-008, agent/ingest.py).

Материал от человека или стороннего аналитика разбирается на три части, а не
уходит целиком в оценку:

  profile_updates     — дополняют слой 0 и остаются между прогонами; автор
                        события в журнале профиля — «материал»;
  baseline_checks     — числа, которыми сверяются допущения слоя 1, с вердиктом
                        по каждому: подтверждает | противоречит | уточняет;
  thesis_candidates   — утверждения о будущем с направлением и условиями
                        (сырьё слоя 2, здесь только собирается).

Якорь указывает на ПЕРВОисточник: материал — носитель знания, путь, которым
число пришло, а не источник числа. Это проверяет код, а не текст промпта:
чужой разбор, названный источником собственного числа, разбор не проходит.

Ориентир прототипа (prototype_fork/ingest.py) воспроизведён секцией про
устаревший баланс: автоисточник отдаёт капитал за прошлый год при доступном
2К26, материал это ловит, и расхождение двух мер базовой линии падает вдвое.

Ответ модели заморожен фикстурой (agent/eval/fixtures/ingest_kspi_answer.md):
эвал не ходит в сеть и не тратит подписку. Живой вызов идёт тем же CLI, что и
на линии, — см. agent.ingest.call_model. Журнал профиля и источник материала
подменяются (AGENT_PROFILE_DB, AGENT_MATERIAL_DB): боевая invest.db не
открывается ни на запись, ни на чтение.

    python3 agent/eval/run_ingest_eval.py   # exit 0, если все PASS
"""
import inspect
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agent import baseline, coherence, ingest, profile, profile_store  # noqa: E402

CLI = str(ROOT / "agent" / "cli.py")
FIXTURE = ROOT / "agent" / "eval" / "fixtures" / "ingest_kspi_answer.md"
GREEN, RED, GREY, RESET = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
FAILURES = []

# Допущения и курс — те же, что в эвале слоя 1: материал сверяется с той же
# базовой линией, иначе сравнение вердиктов ни о чём не говорит.
FX = 0.0021820245310664177
RATE, GROWTH = 0.17, 0.09
RATE_WHY = "ставка ЦБ РК плюс премия за акционерный риск, в тенге"
GROWTH_WHY = "инфляция Казахстана плюс реальный рост экономики"
PRICE = 105.55

# Факты АВТОИСТОЧНИКА: баланс за прошлый год при доступном 2К26 — тот случай,
# который поймал разбор материала. Прибыль, EPS и акции одного отчёта сходятся,
# рассогласован только капитал: когерентность такой набор пропускает, и базовая
# линия считается по устаревшему ROE.
AUTO_SOURCE = dict(
    ticker="KSPI", price=PRICE, price_currency="USD", financial_currency="KZT",
    fx=FX, shares=190_027_266,
    shares_source="190 027 266 акций в обращении, отчёт за 2К26",
    net_income=1_073_180_000_000, eps=5647.51,
    book_value=2_352_000_000_000,
    period_end="2025-12-31", available_end="2025-12-31",
    dps_declared=3994.8,
    dps_declared_source="объявлен компанией вперёд до 1кв27",
    dps_trailing=2630.58,
    price_context={"earnings_data_stale": False,
                   "most_recent_earnings_date": "2026-06-30"},
    source="автоисточник: баланс за FY2025 при доступном 2К26",
)

# Те же отчётные числа с капиталом 2К26 — то, что материал называет вместо
# устаревшего баланса. Ориентир спеки: коридор 117.49–118.77, расхождение ~1%.
REFINED = dict(AUTO_SOURCE, book_value=2_827_400_000_000,
               period_end="2026-06-30", available_end="2026-06-30",
               source="материал: отчёт Kaspi.kz за 2К26")

# Сопоставимые банков из seed профиля: старое значение первого события разбора.
SEED_COMPS = ["ITUB", "HDB", "GGAL", "KB"]

# Материал — выжимка стороннего аналитика, как она лежит в source_digests.
MATERIAL = ingest.Material(
    name="выжимка Алёнки по Kaspi 2К26",
    text="""Отчёт Kaspi.kz за 2К26. Капитал акционеров 2 827.4 млрд KZT на
30.06.2026 — на 20% выше, чем на конец 2025 (2 352.0 млрд). Прибыль за
12 месяцев 1 073.2 млрд KZT, EPS 5 647.5 KZT, ROE 38% по капиталу 2К26.
Компания объявила дивиденд 3 994.8 KZT (8.72 USD) на акцию до 1кв27.

Драйвер прибыли 2027 — разворот фондирования: снижение депозитной ставки
с ноября 2026. Второй контур — запуск финтеха Hepsi Bank в 2027.
Риск: регуляторный потолок комиссий эквайринга.

Сопоставимая группа банков той же природы: ITUB, HDB, GGAL, KB; ближе всех
по юрисдикции — SBER.""")


def check(desc, cond, detail=""):
    if cond:
        print(f"{GREEN}✓ PASS{RESET}  {desc}")
    else:
        FAILURES.append(desc)
        print(f"{RED}✗ FAIL{RESET}  {desc}")
        if detail:
            print(f"         {GREY}{detail}{RESET}")


def _eq(desc, got, want):
    check(desc, got == want, f"получено {got!r}, ожидалось {want!r}")


def baseline_of(**over):
    raw = dict(AUTO_SOURCE)
    raw.update(over)
    return baseline.build("KSPI", coherence.Facts.from_dict(raw), rate=RATE,
                          growth=GROWTH, rate_why=RATE_WHY, growth_why=GROWTH_WHY)


def parse_fixture():
    return ingest.parse(FIXTURE.read_text(encoding="utf-8"), "KSPI", MATERIAL.name)


def _json_of_fixture():
    raw = FIXTURE.read_text(encoding="utf-8")
    return json.loads(raw.split("```json")[1].split("```")[0])


def run_cli(*args, env=None):
    """Команда в отдельном процессе: тот же слой, тот же журнал."""
    return subprocess.run([sys.executable, CLI, *args], capture_output=True,
                          text=True, cwd=ROOT,
                          env={**os.environ, **(env or {})})


def section_frozen():
    """Разбор замороженного ответа: три части, ни одна не пуста."""
    print("\n— замороженный ответ раскладывается на три адресата —")
    check("фикстура замороженного ответа на месте", FIXTURE.exists(), str(FIXTURE))
    split = parse_fixture()
    check("разбор не в отказе", not split.is_refusal(),
          "; ".join(f"{p.code}: {p.message}" for p in split.problems))
    _eq("разбор — объект слоя, а не сырой JSON ответа",
        type(split).__name__, "Split")
    check("первая часть: дополнения профиля", len(split.profile_updates) >= 2,
          f"{len(split.profile_updates)}")
    check("вторая часть: проверки базовой линии", len(split.baseline_checks) >= 3,
          f"{len(split.baseline_checks)}")
    check("третья часть: кандидаты тезисов", len(split.thesis_candidates) >= 2,
          f"{len(split.thesis_candidates)}")
    _eq("ни одна часть не пуста", split.empty_parts, ())
    _eq("тикет разбору приписывает вызывающий, не модель", split.ticker, "KSPI")
    _eq("материал назван по имени: он носитель разбора",
        split.material_name, MATERIAL.name)
    return split


def section_anchors(split):
    """Якорь — первоисточник, материал — путь, которым число пришло."""
    print("\n— якорь на первоисточник, материал — путь —")
    updates = split.profile_updates
    check("у каждого дополнения есть класс якоря",
          all(u.anchor_class for u in updates),
          f"{[u.anchor_class for u in updates]!r}")
    check("класс якоря — из словаря слоя, не из головы модели",
          all(u.anchor_class in ingest.ANCHOR_CLASSES for u in updates),
          f"{[u.anchor_class for u in updates]!r}")
    anchored = [u for u in updates if u.anchor_class != ingest.ANALYST_JUDGEMENT]
    check("подпёртый якорь называет первоисточник",
          all(u.source.strip() for u in anchored),
          f"{[(u.field, u.source) for u in anchored]!r}")
    check("источник — не сам материал",
          all(u.source.strip() != MATERIAL.name for u in updates),
          f"{[u.source for u in updates]!r}")
    check("первоисточник — компания или peer-группа, не чужой разбор",
          all(not ingest._names_carrier(u.source) for u in updates),
          f"{[u.source for u in updates]!r}")
    check("путь, которым число пришло, ставит слой, а не модель",
          all(u.via == MATERIAL.name for u in updates),
          f"{[u.via for u in updates]!r}")

    checks = split.baseline_checks
    check("у каждой проверки базовой линии якорь и первоисточник тоже есть",
          all(c.anchor_class in ingest.ANCHOR_CLASSES and c.source.strip()
              for c in checks),
          f"{[(c.key, c.source) for c in checks]!r}")
    check("число базовой линии не ссылается на материал как на источник",
          all(c.source.strip() != MATERIAL.name for c in checks))


def section_checks(split):
    """Вердикт по каждому числу, сверенный с базовой линией кодом."""
    print("\n— проверки базовой линии: вердикт по каждому числу —")
    _eq("словарь вердиктов объявлен", ingest.VERDICTS,
        ("подтверждает", "противоречит", "уточняет"))
    check("у каждой проверки есть вердикт из словаря",
          all(c.verdict in ingest.VERDICTS for c in split.baseline_checks),
          f"{[(c.key, c.verdict) for c in split.baseline_checks]!r}")
    check("число у каждой проверки положительное",
          all(isinstance(c.value, float) and c.value > 0
              for c in split.baseline_checks),
          f"{[(c.key, c.value) for c in split.baseline_checks]!r}")
    check("ключ числа — из словаря величин",
          all(c.key in ingest.CHECK_KEYS for c in split.baseline_checks),
          f"{[c.key for c in split.baseline_checks]!r}")
    covered = {c.key for c in split.baseline_checks}
    check("проверены и дивиденд, и капитал, и мультипликатор — не одна величина",
          {"dps", "bvps", "pbv"} <= covered, f"{covered!r}")
    check("противоречие и уточнение объяснены: без «почему» они не читаются",
          all(c.note.strip() for c in split.baseline_checks
              if c.verdict != "подтверждает"),
          f"{[(c.key, c.verdict, c.note) for c in split.baseline_checks]!r}")

    base = baseline_of()
    check("базовая линия автоисточника считается: иначе сверять не с чем",
          not base.is_refusal(), "; ".join(m.code for m in base.mismatches))
    compared = ingest.compare(split, base)
    check("сверка не добавила отказов: вердикты согласованы с числами",
          not compared.is_refusal(),
          "; ".join(f"{p.code}: {p.message}" for p in compared.problems))
    by_key = {c.key: c for c in compared.baseline_checks}
    check("слой проставил базовое значение рядом с числом материала",
          all(c.baseline_value is not None for c in compared.baseline_checks),
          f"{[(c.key, c.baseline_value) for c in compared.baseline_checks]!r}")

    # 8.72 USD — валюта котировки; допущения базовой линии в валюте отчётности,
    # поэтому сравнение обязано пройти через объявленный курс, а не мимо него.
    dps = by_key["dps"]
    _eq("дивиденд материала приведён к валюте отчётности (D20)",
        dps.value_financial, round(8.72 / FX, 2))
    check("само число материала сохранено как сказано",
          (dps.value, dps.unit), (8.72, "USD"))
    check("дивиденд подтверждает допущение: расхождение в пределах порога",
          dps.verdict == "подтверждает" and abs(dps.delta) <= ingest.CHECK_TOLERANCE,
          f"база {dps.baseline_value}, материал {dps.value_financial}, "
          f"delta={dps.delta!r}")
    _eq("порог согласия числа объявлен, а не спрятан в сравнении",
        ingest.CHECK_TOLERANCE, 0.10)
    check("капитал противоречит допущению: автоисточник отдаёт прошлый год",
          by_key["bvps"].verdict == "противоречит"
          and by_key["bvps"].delta > ingest.CHECK_TOLERANCE,
          f"база {by_key['bvps'].baseline_value}, материал "
          f"{by_key['bvps'].value_financial}, delta {by_key['bvps'].delta!r}")
    check("ROE уточнён материалом, и разница видна числом",
          by_key["roe"].verdict == "уточняет" and by_key["roe"].delta is not None
          and by_key["roe"].delta > ingest.CHECK_TOLERANCE,
          f"база {by_key['roe'].baseline_value}, материал "
          f"{by_key['roe'].value_financial}")
    check("прибыль на акцию сходится с допущением обоих наборов",
          by_key["eps"].verdict == "подтверждает",
          f"eps: база {by_key['eps'].baseline_value}, материал "
          f"{by_key['eps'].value_financial}")
    check("мультипликатор противоречит: посчитан на устаревшем ROE",
          by_key["pbv"].verdict == "противоречит"
          and by_key["pbv"].delta > ingest.CHECK_TOLERANCE,
          f"база {by_key['pbv'].baseline_value}, материал "
          f"{by_key['pbv'].value_financial}")
    return compared


LYING_CONTRADICTS = """```json
{"profile_updates": [],
 "baseline_checks": [{"key": "dps", "what": "дивиденд на акцию",
    "value": 8.72, "unit": "USD", "verdict": "противоречит",
    "note": "не то", "anchor_class": "company_guide",
    "source": "гайд Kaspi.kz до 1кв27"}],
 "thesis_candidates": []}"""


def section_lying_verdict():
    """Вердикт, противоречащий арифметике, — отказ, а не цитата модели."""
    print("\n— вердикт, противоречащий числу, слой не пропускает —")
    lying = """```json
{"profile_updates": [],
 "baseline_checks": [{"key": "bvps", "what": "капитал на акцию",
    "value": 14878.6, "unit": "KZT", "verdict": "подтверждает",
    "note": "как же", "anchor_class": "company_guide",
    "source": "баланс Kaspi.kz на 30.06.2026"}],
 "thesis_candidates": []}"""
    compared = ingest.compare(ingest.parse(lying, "KSPI", MATERIAL.name),
                              baseline_of())
    m = [p for p in compared.problems if p.code == "verdict"]
    check("«подтверждает» при расхождении в пятую часть — отказ",
          bool(m), f"{[p.message for p in compared.problems]!r}")
    check("отказ показывает оба числа и расхождение",
          m and "14 878.6" in m[0].message and "20%" in m[0].message,
          m[0].message if m else "—")
    agree = ingest.compare(ingest.parse(LYING_CONTRADICTS, "KSPI", MATERIAL.name),
                           baseline_of())
    check("«противоречит» при сходящихся числах — тоже отказ",
          any(p.code == "verdict" for p in agree.problems),
          f"{[p.message for p in agree.problems]!r}")


def section_stale_balance(split):
    """Ориентир прототипа: материал ловит устаревший баланс, меры сходятся."""
    print("\n— материал как проверка данных: баланс прошлого года против 2К26 —")
    stale = baseline_of()
    refined = baseline_of(book_value=REFINED["book_value"],
                          period_end=REFINED["period_end"],
                          available_end=REFINED["available_end"])
    check("устаревшая база считается: когерентность такой набор пропускает",
          not stale.is_refusal() and not refined.is_refusal(),
          "; ".join(m.code for m in stale.mismatches + refined.mismatches))
    check("на устаревшем балансе меры расходятся заметно",
          stale.divergence > 0.03, f"расхождение {stale.divergence:.1%}")
    check("после уточнения капитала расхождение упало более чем вдвое",
          refined.divergence < stale.divergence / 2,
          f"{stale.divergence:.1%} → {refined.divergence:.1%}")
    check("уточнённый коридор — ориентир спеки 117.49–118.77",
          abs(refined.corridor[0] - 117.49) < 0.05
          and abs(refined.corridor[1] - 118.77) < 0.01,
          f"коридор {refined.corridor!r}")
    bvps = [c for c in ingest.compare(split, stale).baseline_checks
            if c.key == "bvps"][0]
    check("материал называет капитал 2К26 вместо баланса прошлого года",
          bvps.verdict == "противоречит" and bvps.note,
          f"{bvps.verdict!r}, note: {bvps.note!r}")
    check("уточнение несёт якорь на отчёт, а не на материал",
          "Kaspi" in bvps.source and bvps.source != MATERIAL.name, bvps.source)


def section_theses(split):
    print("\n— кандидаты тезисов: направление и условия —")
    _eq("словарь направлений — те же метки, что у сценариев",
        ingest.DIRECTIONS, ("bull", "base", "bear"))
    check("у каждого кандидата направление из словаря",
          all(t.direction in ingest.DIRECTIONS for t in split.thesis_candidates),
          f"{[(t.direction, t.thesis[:40]) for t in split.thesis_candidates]!r}")
    check("есть оба направления: и апсайд, и риск",
          {"bull", "bear"} <= {t.direction for t in split.thesis_candidates},
          f"{[t.direction for t in split.thesis_candidates]!r}")
    check("у каждого кандидата тезис и условие, проверяемое следующим отчётом",
          all(t.thesis.strip() and t.must_be_true for t in split.thesis_candidates),
          f"{[t.must_be_true for t in split.thesis_candidates]!r}")
    known = {d for u in split.profile_updates if u.field == "drivers"
             for d in u.value} | set(profile.build("KSPI").drivers)
    check("тезисы названы в драйверах этого бизнеса, а не в абстрактном потоке",
          all(set(t.drivers_touched) <= known for t in split.thesis_candidates),
          f"известно {known!r}, названо "
          f"{[t.drivers_touched for t in split.thesis_candidates]!r}")


def section_profile(split):
    print("\n— дополнения профиля попадают в события с автором «материал» —")
    _eq("в журнал пока ничего не записано: запись — отдельный шаг",
        profile_store.timeline("KSPI"), [])
    written = ingest.apply("KSPI", split)
    _eq("записано ровно столько событий, сколько дополнений",
        len(written), len(split.profile_updates))
    _eq("каждое дополнение вступило в силу: мера среди них не предложена",
        all(applied for _f, applied in written), True)
    events = profile_store.timeline("KSPI")
    _eq("поля событий — поля разбора", [e["field"] for e in events],
        [u.field for u in split.profile_updates])
    check("автор каждого события — «материал»",
          all(e["author"] == "material" for e in events),
          f"{[e['author'] for e in events]!r}")
    check("у каждого события причина из разбора",
          all(e["reason"] for e in events),
          f"{[e['reason'][:40] for e in events]!r}")
    _eq("старое значение — то, что было в профиле до материала",
        events[0]["old_value"], SEED_COMPS)

    p = profile.build("KSPI")
    _eq("сопоставимые из материала перекрыли seed",
        p.comps, tuple([u for u in split.profile_updates
                        if u.field == "comps"][0].value))
    _eq("пробелы данных дополнены",
        p.data_gaps, tuple([u for u in split.profile_updates
                            if u.field == "data_gaps"][0].value))
    _eq("мера при этом осталась из профиля", p.measure, profile.DDM_RI)
    _eq("в журнале не больше событий, чем дополнений",
        len(profile_store.timeline("KSPI")), len(written))


def section_measure_by_material():
    print("\n— материал не меняет меру: смена меры ждёт человека —")
    split = ingest.parse(MEASURE_PROPOSAL, "KSPI", MATERIAL.name)
    check("разбор предложения состоялся", not split.is_refusal(),
          "; ".join(p.message for p in split.problems))
    written = ingest.apply("KSPI", split)
    _eq("событие записано", len(written), 1)
    _eq("смена меры в силу не вступила", written[0][1], False)
    _eq("мера в профиле прежняя", profile.build("KSPI").measure, profile.DDM_RI)
    _eq("предложение ждёт решения человека",
        [i["field"] for i in profile_store.replay("KSPI")[1]], ["measure"])
    ev = profile_store.timeline("KSPI")[-1]
    _eq("автор предложения — «материал», не модель", ev["author"], "material")
    _eq("событие неподтверждённое", ev["confirmed"], 0)
    check("тезисы из разбора никуда не пишутся: это сырьё слоя 2",
          all(e["field"] not in ingest.DIRECTIONS
              for e in profile_store.timeline("KSPI")),
          f"{[e['field'] for e in profile_store.timeline('KSPI')]!r}")


MEASURE_PROPOSAL = """```json
{"profile_updates": [{"field": "measure", "value": "ev_revenue",
   "reason": "аналитик считает Kaspi процессингом, а не банком",
   "anchor_class": "peer_stats",
   "source": "статистика peer-группы процессинга"}],
 "baseline_checks": [], "thesis_candidates": []}"""



def section_refusals():
    """Структурные проверки в коде: промпт не умеет отказывать."""
    print("\n— структурные отказы —")
    check("ответ без JSON — отказ, а не пустой разбор",
          ingest.parse("мне кажется, материал хороший", "KSPI", "м").is_refusal())
    check("отказ объясняет, чего не хватило",
          any(p.code == "answer" for p in
              ingest.parse("не JSON", "KSPI", "м").problems),
          f"{[p.message for p in ingest.parse('не JSON', 'KSPI', 'м').problems]!r}")
    check("JSON без одной из трёх частей — отказ",
          ingest.parse('{"profile_updates": []}', "KSPI", "м").is_refusal())

    def broken(part, items):
        raw = _json_of_fixture()
        raw[part] = items
        return ingest.parse(
            "```json\n%s\n```" % json.dumps(raw, ensure_ascii=False),
            "KSPI", MATERIAL.name)

    def problems_of(part, items):
        split = broken(part, items)
        return [p.code for p in split.problems]

    def checked_problems_of(part, items):
        """Отказы после сверки: часть проверок знает только базовая линия."""
        split = broken(part, items)
        compared = ingest.compare(split, baseline_of())
        return [p.code for p in compared.problems]

    check("якорь «материал» — чужой разбор назван источником собственного числа",
          "anchor" in problems_of("profile_updates",
                                  [{"field": "comps", "value": ["KB"],
                                    "reason": "группа шире",
                                    "anchor_class": "материал",
                                    "source": MATERIAL.name}]))
    check("источник, совпадающий с материалом, — та же подмена",
          "anchor" in problems_of("profile_updates",
                                  [{"field": "comps", "value": ["KB"],
                                    "reason": "группа шире",
                                    "anchor_class": "peer_stats",
                                    "source": MATERIAL.name}]))
    check("источник, названный выжимкой, — тот же носитель вместо источника",
          "anchor" in problems_of("baseline_checks",
                                  [{"key": "dps", "what": "дивиденд",
                                    "value": 8.72, "unit": "USD",
                                    "verdict": "подтверждает",
                                    "anchor_class": "company_guide",
                                    "source": "выжимка Алёнки по Kaspi"}]))
    check("неизвестный класс якоря не проходит",
          "anchor" in problems_of("baseline_checks",
                                  [{"key": "dps", "what": "дивиденд",
                                    "value": 8.72, "unit": "USD",
                                    "verdict": "подтверждает",
                                    "anchor_class": "chat_gpt",
                                    "source": "x"}]))
    check("подпёртый якорь без первоисточника не проходит",
          "anchor" in problems_of("profile_updates",
                                  [{"field": "comps", "value": ["KB"],
                                    "reason": "группа шире",
                                    "anchor_class": "peer_stats",
                                    "source": "  "}]))
    check("проверка без вердикта — отказ: вердикт и есть результат",
          "verdict" in problems_of("baseline_checks",
                                   [{"key": "dps", "what": "дивиденд",
                                     "value": 8.72, "unit": "USD",
                                     "anchor_class": "company_guide",
                                     "source": "гайд"}]))
    check("число в валюте, которой нет ни в цене, ни в отчётности, — отказ",
          "unit" in checked_problems_of("baseline_checks",
                                        [{"key": "dps", "what": "дивиденд",
                                          "value": 8.72, "unit": "EUR",
                                          "verdict": "подтверждает",
                                          "anchor_class": "company_guide",
                                          "source": "гайд"}]))
    check("SPC-009 §3.3: источник называет эмитента, класс analyst_judgement — отказ",
          "anchor" in problems_of("profile_updates",
                                  [{"field": "comps", "value": ["KB"],
                                    "reason": "по гайду компании",
                                    "anchor_class": "analyst_judgement",
                                    "source": "KSPI investor relations"}]))
    check("источник называет эмитента, класс peer_stats — тоже отказ",
          "anchor" in problems_of("baseline_checks",
                                  [{"key": "dps", "what": "дивиденд",
                                    "value": 8.72, "unit": "USD",
                                    "verdict": "подтверждает",
                                    "anchor_class": "peer_stats",
                                    "source": "KSPI annual report"}]))
    check("источник называет эмитента, класс company_guide — проходит",
          "anchor" not in problems_of("profile_updates",
                                      [{"field": "comps", "value": ["KB"],
                                        "reason": "по гайду компании",
                                        "anchor_class": "company_guide",
                                        "source": "KSPI annual report"}]))
    check("тезис без условий — отказ: это настроение, а не тезис",
          "thesis" in problems_of("thesis_candidates",
                                  [{"direction": "bull", "thesis": "вырастет",
                                    "must_be_true": []}]))
    check("неизвестное направление тезиса — отказ",
          "thesis" in problems_of("thesis_candidates",
                                  [{"direction": "moon", "thesis": "к луне",
                                    "must_be_true": ["отчёт"]}]))
    check("дополнение чужим полем профиля не пишется",
          "field" in problems_of("profile_updates",
                                 [{"field": "ticker", "value": "KSPI",
                                   "reason": "а так",
                                   "anchor_class": "company_guide",
                                   "source": "отчёт"}]))
    check("у отказавшего разбора части пусты и записи из него нет",
          all(part == () for part in (ingest.parse("мимо", "KSPI", "м")
                                      .profile_updates,
                                      ingest.parse("мимо", "KSPI", "м")
                                      .baseline_checks,
                                      ingest.parse("мимо", "KSPI", "м")
                                      .thesis_candidates)))

    print("\n— контракты слоя —")
    src = inspect.getsource(ingest)
    check("слой сам не ходит в сеть: нет ни yfinance, ни requests, ни urllib",
          not any(w in src for w in ("yfinance", "requests", "urllib",
                                     "urlopen", "http")))


def section_material_source(state):
    print("\n— материал читается из source_digests только на чтение —")
    check("в БД без таблиц материал отсутствует, а не падение",
          ingest.latest_material("KSPI", state["empty_db"]) is None)
    check("файла БД нет — тоже нет материала, а не падение",
          ingest.latest_material("KSPI", state["missing_db"]) is None)
    got = ingest.latest_material("KSPI", state["digest_db"])
    check("последняя выжимка берётся по тикеру", got is not None
          and got.text == "свежий текст", f"{got!r}")
    _eq("имя материала — source_name из таблицы", got.name, "выжимка 2")
    before = state["digest_db"].stat().st_mtime_ns
    ingest.latest_material("KSPI", state["digest_db"])
    check("чтение не меняет файл", state["digest_db"].stat().st_mtime_ns == before)


def section_command(state):
    print("\n— команда разбирает по замороженному ответу и печатает решение —")
    with tempfile.TemporaryDirectory() as td:
        mat = Path(td) / "material.txt"
        mat.write_text(MATERIAL.text, encoding="utf-8")
        env = {"AGENT_MATERIAL_DB": str(state["digest_db"])}
        out = run_cli("ingest", "KSPI", "--material", str(mat),
                      "--source-name", MATERIAL.name, "--answer", str(FIXTURE))
        check("команда завершилась без ошибки", out.returncode == 0,
              f"rc={out.returncode}, stderr={out.stderr.strip()[-300:]}")
        check("в выводе все три части",
              all(w in out.stdout for w in ("профиль", "базовую линию", "тезис")),
              out.stdout[:600])
        check("в выводе автор событий — материал", "материал" in out.stdout)
        check("в выводе вердикты по числам",
              "подтверждает" in out.stdout and "противоречит" in out.stdout,
              out.stdout[:900])
        check("в выводе путь числа: материал назван носителем",
              MATERIAL.name in out.stdout, out.stdout[-400:])
        fields = [u.field for u in parse_fixture().profile_updates]
        events = profile_store.timeline("KSPI")
        check("команда записала те же дополнения, что и слой напрямую",
              [e["field"] for e in events][-len(fields):] == fields,
              f"{[e['field'] for e in events]!r}")

        print("\n— материал берётся из source_digests, когда файла нет —")
        out = run_cli("ingest", "KSPI", "--answer", str(FIXTURE), env=env)
        check("материал прочитан из БД выжимок", out.returncode == 0,
              f"rc={out.returncode}, stderr={out.stderr.strip()[-300:]}")
        check("в выводе назван источник материала", "выжимка 2" in out.stdout,
              out.stdout[:300])
        before = len(profile_store.timeline("KSPI"))

        print("\n— команда на сломанном ответе: отказ, не запись —")
        bad = Path(td) / "bad.txt"
        bad.write_text("не JSON", encoding="utf-8")
        out = run_cli("ingest", "KSPI", "--material", str(mat), "--answer", str(bad))
        check("отказ слоя — код 3", out.returncode == 3,
              f"rc={out.returncode}, stdout={out.stdout[:200]}")
        check("в отказе названа причина", "JSON" in out.stdout,
              out.stdout[:300])
        _eq("в отказе ничего не записано", len(profile_store.timeline("KSPI")), before)

        print("\n— без материала команда не разбирает —")
        empty_env = {"AGENT_MATERIAL_DB": str(state["empty_db"])}
        out = run_cli("ingest", "KSPI", "--answer", str(FIXTURE), env=empty_env)
        check("нет материала — ошибка вызывающего, код 2", out.returncode == 2,
              f"rc={out.returncode}, stdout={out.stdout[:200]}")
        check("ошибка говорит, где взять материал",
              "материал" in out.stderr, out.stderr[:300])
        out = run_cli("ingest", "NOPE", "--material", str(mat),
                      "--answer", str(FIXTURE))
        check("неизвестный тикер — ошибка команды", out.returncode == 2,
              f"rc={out.returncode}")




def _args(**kw):
    """Namespace аргументов команды: cmd_ingest зовётся здесь, не через subprocess."""
    defaults = dict(ticker="KSPI", material=None, source_name=None, answer=None,
                    out=None, facts=None, rate=None, growth=None, rate_why=None,
                    growth_why=None)
    defaults.update(kw)
    from argparse import Namespace
    return Namespace(**defaults)


def _invest_state():
    """Состояние боевой БД: слой v2 не имеет права её менять."""
    path = ROOT / "invest.db"
    if not path.exists():
        return None
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def _digest_db(td):
    """Отдельная БД с той же схемой source_digests — для проверки чтения."""
    path = Path(td) / "digests.db"
    con = sqlite3.connect(path)
    try:
        con.execute("""create table source_digests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL,
            source_name TEXT NOT NULL, created_at TEXT NOT NULL,
            provider TEXT NOT NULL, model TEXT NOT NULL,
            digest_text TEXT NOT NULL, status TEXT NOT NULL)""")
        con.execute("insert into source_digests (ticker, source_name, created_at,"
                    " provider, model, digest_text, status) values"
                    " ('KSPI','выжимка 1','2026-08-01','p','m','старый текст','ok'),"
                    " ('KSPI','выжимка 2','2026-08-29','p','m','свежий текст','ok'),"
                    " ('DELL','выжимка 3','2026-08-29','p','m','чужой тикер','ok')")
        con.commit()
    finally:
        con.close()
    return path


@patch("agent.baseline.prisms.gate", return_value=None)
def main(_prisms_gate) -> int:
    print("\nОфлайн-регрессия разбора материала — три адресата "
          "(agent/ingest.py)\n")
    before = _invest_state()
    with tempfile.TemporaryDirectory() as td:
        os.environ["AGENT_PROFILE_DB"] = str(Path(td) / "agent.db")
        empty = Path(td) / "empty.db"
        sqlite3.connect(empty).close()
        state = {
            "empty_db": empty,
            "missing_db": Path(td) / "no" / "such.db",
            "digest_db": _digest_db(td),
        }
        try:
            split = section_frozen()
            section_anchors(split)
            section_checks(split)
            section_lying_verdict()
            section_stale_balance(split)
            section_theses(split)
            section_profile(split)
            section_measure_by_material()
            section_refusals()
            section_material_source(state)
            section_command(state)
        finally:
            os.environ.pop("AGENT_PROFILE_DB", None)
    check("боевая invest.db не изменилась ни байтом", _invest_state() == before,
          f"было {before}, стало {_invest_state()}")

    print()
    if FAILURES:
        print(f"{RED}{len(FAILURES)} FAIL{RESET}")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"{GREEN}Все PASS{RESET} — материал разбирается по трём адресатам, "
          f"дополняет профиль событиями и сверяет базовую линию с вердиктом "
          f"по каждому числу.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
