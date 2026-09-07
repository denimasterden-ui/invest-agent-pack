"""Слой 2 v2 — тезис о будущем как ветка базовой линии (SPC-008).

Базовая линия держит набор предпосылок и считает их доверенной формулой. Форк
не вводит новой арифметики: он наследует её предпосылки, переопределяет их
подмножество — каждое переопределение с обоснованием и якорем — и коридор
считает ТЕМ ЖЕ кодом, что и базовую линию. Границы стоимости модель не
объявляет вовсе: в контракте форка для них нет места, а ответ, их содержащий,
отклоняется разбором. Иначе «заявлено против пересчитано» не поймать —
именно так v1 получала полосу P/E из головы, помеченную ``analyst_judgement``.

Якорь — два поля, не одно. ``anchor_class`` отвечает, чей это источник;
``confirms`` — что источник подтверждает: само число, драйвер (число вывел
аналитик) или ничего. Живой прогон 24.08 показал, зачем второе: модель ставила
company_guide на темп роста, хотя компания называла драйвер (снижение ставки
по вкладам), а темпы выводил аналитик. Класс «суждение аналитика» совместим
только с «собственной оценкой» — иначе форк утверждает несуществующий источник.

Требование якоря адресное. Подпёрта должна быть величина, несущая тезис
(прибыль, дивиденд, темп роста): без неё ставка — фантазия. Полосы оценки
(границы мультипликатора, ставка, терминал) могут остаться суждением: коридор
тогда помечается оценочным, но ставка не отменяется — иначе правило блокировало
бы и легитимные тезисы.

Каналов три: поток, прибыль, дивиденд. Выбор канала — часть работы модели, он
объявляется с основанием и обязан мерить то, что меняется по тезису. Без этой
проверки модель уходит в единственный канал, форма которого подана готовой,
даже когда он для этой бумаги неверен: DELL 24.08 считался потоком, когда
менялась прибыль сегмента, а KSPI — потоком, которого у банка нет.

Слой сам в сеть не ходит и ничего не хранит: вход — собранная базовая линия
(публичный вход слоя 1) и ответ модели. Физические потолки остаются слою
базовой линии — у форка нет драйверов, чтобы их проверять.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import re
from dataclasses import dataclass

from . import measures, profile
from .kernel import store_client

# ── каналы: чем меряется тезис ───────────────────────────────────────────────

CASH_FLOW = "поток"          # дисконтированный поток на переопределённой траектории
EARNINGS = "прибыль"         # прибыль на акцию вперёд × полоса мультипликатора
DIVIDEND = "дивиденд"        # объявленная выплата на требуемую дивдоходность
CHANNELS = (CASH_FLOW, EARNINGS, DIVIDEND)

# Формат fork-answer, который аналитик (Claude) готовит для `cli fork --answer`.
# Границы стоимости не объявляются — их считает код. Сохранён как reference
# формата (был ROLE-промпт gen_forks; сама генерация теперь на аналитике).
ANSWER_FORMAT = """Верни JSON-объект с ключом forks: bull- и bear-тезисы о будущем.
corridor/target_price/fair_value не объявляй — их считает код.
Каждый override: value, rationale, anchor_class, confirms, source.
Рядом с форком верни recheck: по объекту на каждое условие must_be_true, с
дословным condition, status (сработало/не сработало/пока не проверить/перенесено)
и строкой fact. Если текущий факт уже хуже порога — status «не сработало»: не
скрывай отставание повышением прогнозной величины.
Для bear якорь бери у факта риска, не у выведенного EPS: концентрация выручки,
capex-зависимость, churn из отчётности — company_guide; выведенный из них
eps_forward наследует company_guide/driver с источником-фактом (не
analyst_judgement/estimate).
Каналы: поток, прибыль, дивиденд. Для SOTP разрешён только «поток»: EPS × PE
запрещён (не видит cash и стейки). SOTP-форк переопределяет growth_path,
terminal_fcf_margin, terminal_fcf_multiple, discount_rate и stakes (полный
список долей: ownership_pct и entity_valuation.base)."""

# ── предпосылки, которые форк переопределяет ─────────────────────────────────

DISCOUNT_RATE = "discount_rate"
GROWTH_PATH = "growth_path"
TERMINAL_GROWTH = "terminal_growth"
TERMINAL_FCF_MARGIN = "terminal_fcf_margin"
TERMINAL_FCF_MULTIPLE = "terminal_fcf_multiple"
STAKES = "stakes"
EPS_FORWARD = "eps_forward"
PE_LOW = "pe_low"
PE_HIGH = "pe_high"
DPS_FORWARD = "dps_forward"
REQUIRED_YIELD = "required_yield"

FLOW_FIELDS = (DISCOUNT_RATE, GROWTH_PATH, TERMINAL_GROWTH,
               TERMINAL_FCF_MARGIN, TERMINAL_FCF_MULTIPLE)
SOTP_CORE_FIELDS = (GROWTH_PATH, TERMINAL_FCF_MARGIN,
                    TERMINAL_FCF_MULTIPLE, DISCOUNT_RATE)
SOTP_FIELDS = SOTP_CORE_FIELDS + (STAKES,)
PE_FIELDS = (EPS_FORWARD, PE_LOW, PE_HIGH)
DIV_FIELDS = (DPS_FORWARD, REQUIRED_YIELD)
CHANNEL_FIELDS = {CASH_FLOW: FLOW_FIELDS, EARNINGS: PE_FIELDS, DIVIDEND: DIV_FIELDS}
_FIELD_CHANNEL = {name: channel
                  for channel, names in CHANNEL_FIELDS.items() for name in names}
_FIELD_CHANNEL[STAKES] = CASH_FLOW
OVERRIDABLE = FLOW_FIELDS + (STAKES,) + PE_FIELDS + DIV_FIELDS

# Что несёт тезис, а что — суждение об оценке. Аналитик утверждает событие
# («прибыль вырастет из-за разворота фондирования») и отдельно прикидывает, как
# рынок эту прибыль оценит. Первое требует якоря, второе — нет.
DRIVING_FIELDS = (EPS_FORWARD, DPS_FORWARD, GROWTH_PATH)
BAND_FIELDS = (PE_LOW, PE_HIGH, REQUIRED_YIELD, DISCOUNT_RATE, TERMINAL_GROWTH)

BULL, BEAR = "bull", "bear"
LABELS = (BULL, BEAR)

# ── что именно подтверждает источник ─────────────────────────────────────────

NUMBER = "number"      # источник называет само число
DRIVER = "driver"      # источник называет событие, число оцифровал аналитик
ESTIMATE = "estimate"  # источника нет вовсе, число выбрал аналитик
CONFIRMS = (NUMBER, DRIVER, ESTIMATE)

# ── классы якорей ────────────────────────────────────────────────────────────
# Та же лексика, на которой построен разбор материала (agent.ingest): материал
# — носитель знания, класс отвечает за первоисточник.
COMPANY_GUIDE = "company_guide"          # гайд, заявление или отчётность эмитента
MACRO_GUIDANCE = "macro_guidance"        # ВВП, ставка ЦБ, гайденс регулятора
PEER_STATS = "peer_stats"                # статистика конкурентов / peer-группы
OWN_HISTORY = "own_history"              # собственный мультипликатор бумаги
ANALYST_JUDGEMENT = "analyst_judgement"  # источника нет
ANCHOR_CLASSES = (COMPANY_GUIDE, MACRO_GUIDANCE, PEER_STATS, OWN_HISTORY,
                  ANALYST_JUDGEMENT)
ANCHORED = tuple(c for c in ANCHOR_CLASSES if c != ANALYST_JUDGEMENT)

# Ключи, которыми ответ модели объявляет готовую стоимость. В контракте форка
# таких полей нет: их появление — попытка выдать объявленное за посчитанное.
VALUE_BOUND_KEYS = ("corridor", "low", "high", "value_low", "value_high",
                    "price_low", "price_high", "target", "target_price",
                    "fair_value", "valuation")

# Provider-side structured output contract.  It deliberately covers the fields
# whose omission/invalid vocabulary used to be repaired by parser defaults; the
# deterministic business checks below remain the authority for ranges, anchors
# and channel coherence.
FORKS_JSON_SCHEMA = {
    "name": "generated_forks",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "forks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string", "enum": list(LABELS)},
                        "channel": {"type": "string", "enum": list(CHANNELS)},
                        "channel_reason": {"type": "string", "minLength": 1},
                        "overrides": {
                            "type": "object",
                            "additionalProperties": {
                                "type": "object",
                                "properties": {
                                    "anchor_class": {
                                        "type": "string",
                                        "enum": list(ANCHOR_CLASSES),
                                    },
                                    "confirms": {
                                        "type": "string",
                                        "enum": list(CONFIRMS),
                                    },
                                },
                                "required": ["anchor_class", "confirms"],
                            },
                        },
                    },
                    "required": ["label", "channel", "channel_reason",
                                 "overrides"],
                },
                "minItems": 2,
                "maxItems": 2,
            },
        },
        "required": ["forks"],
    },
}


@dataclass(frozen=True)
class Problem:
    """Одно нарушение формы ответа модели."""
    code: str        # answer | bounds | fork | override
    message: str


@dataclass(frozen=True)
class Override:
    """Одно переопределение предпосылки: значение + чем оно подпёрто.

    ``eps_forward`` и ``dps_forward`` объявляются в валюте отчётности — в
    валюту котировки их переводит код тем же курсом, которым переведена
    базовая линия (D20). Наследованные величины переводить не нужно: слой 1
    уже сделал это, и второй курс умножил бы дивиденд дважды.
    """
    value: object
    rationale: str
    anchor_class: str
    source: str
    confirms: str = NUMBER


@dataclass(frozen=True)
class Fork:
    """Ветка базовой линии под названный тезис о будущем."""
    label: str                  # 'bull' | 'bear'
    thesis: str
    channel: str                # 'поток' | 'прибыль' | 'дивиденд'
    channel_reason: str         # почему канал меряет то, что меняется по тезису
    horizon_months: int
    must_be_true: tuple
    overrides: dict             # имя предпосылки -> Override
    deferred_from: int | None = None   # прежний горизонт, если тезис перенесён


@dataclass(frozen=True)
class Basis:
    """Что форк наследует: базовая линия и предпосылки, из которых она посчитана."""
    ticker: str
    measure: str
    measure_reason: str
    price: float
    price_currency: str
    corridor: tuple
    assumptions: dict | None = None   # объявленные предпосылки меры, если она на них считается
    dps: float | None = None          # дивиденд вперёд УЖЕ в валюте котировки: его перевёл слой 1
    fx: float = 1.0             # отчётность → котировка, тем же курсом, что слой 1

    @property
    def has_flow(self):
        """Несёт ли мера предпосылки потока — иначе каналу потока нечем считаться."""
        return self.assumptions is not None


@dataclass(frozen=True)
class Paid:
    """Доля тезиса, которую оплатил рынок, и то, что она значит словами."""
    share: float | None         # None — посчитать не на чем
    label: str


@dataclass(frozen=True)
class Result:
    """Исход форка: коридор из предпосылок, либо отказ без единого числа."""
    fork: Fork
    ticker: str
    measure: str
    channel: str
    corridor: tuple | None = None
    refusals: tuple = ()        # пусто → форк посчитан
    unanchored: tuple = ()      # переопределения на одном суждении
    estimated: tuple = ()       # границы оценки без источника: коридор оценочный
    derived: tuple = ()         # числа, выведенные аналитиком из подтверждённого драйвера
    warnings: tuple = ()        # предупреждения направления — не отказ
    bettable: bool = True       # False → форк остаётся наблюдением
    expected: dict | None = None  # доходность от цены к коридору за срок форка
    peer_band: object = None    # PeerBand из agent.peers: полоса сопоставимых как якорь

    def is_refusal(self):
        return bool(self.refusals)


# ── базовая линия как основа форка ───────────────────────────────────────────

def basis(result, price=None, price_currency=None):
    """Собрать то, что форк наследует, из базовой линии (публичный вход слоя 1).

    Слой мер отдаёт свои объявленные предпосылки как словарь, слой банка —
    детали расчёта, где уже есть объявленный вперёд дивиденд. Цену и валюту
    котировки слой мер не несёт вовсе: тогда их объявляет вызывающий — без
    цены не видно ни направления форка, ни доли оплаченного тезиса. Отказавшая
    базовая линия ветвится во что угодно — на ней форка не строит.
    """
    if result.is_refusal():
        raise ValueError(f"{result.ticker}: базовая линия отказала — "
                         f"форк не на чем ветвить")
    if result.corridor is None:
        raise ValueError(f"{result.ticker}: у базовой линии нет коридора")
    quoted = getattr(result, "price", None) or price
    if not quoted:
        raise ValueError(f"{result.ticker}: базовая линия цены не несёт — "
                         f"объявите её, иначе направление форка и доля "
                         f"оплаченного тезиса не считаются")
    currency = getattr(result, "price_currency", None) or price_currency
    if not currency:
        raise ValueError(f"{result.ticker}: базовая линия не назвала валюту "
                         f"котировки — объявите её")
    detail = result.detail
    assumptions = detail if isinstance(detail, dict) else None
    return Basis(ticker=result.ticker, measure=result.measure,
                 measure_reason=result.measure_reason, price=quoted,
                 price_currency=currency, corridor=result.corridor,
                 assumptions=assumptions,
                 dps=getattr(detail, "dps", None) if assumptions is None else None,
                 fx=getattr(detail, "fx", None) or 1.0)


# ── ответ модели → форки ─────────────────────────────────────────────────────

def parse(answer):
    """Ответ модели → форки. Форму проверяет код, а не текст роли.

    Отказ собирается из всех нарушений сразу. Форк, объявивший границы
    стоимости, отклоняется целиком: он пытается выдать готовую оценку за
    ветку, а переопределять при этом нечего.
    """
    data, problems = _json_answer(answer)
    if data is None:
        return (), problems
    problems += _bounds(data)
    items = data.get("forks")
    if not isinstance(items, list) or not items:
        return (), problems + (Problem(
            "answer", "в ответе нет ни одного форка — ждёт ключ 'forks' со "
                      "списком"),)
    out = []
    for i, item in enumerate(items):
        declared = _bounds(item)
        if declared:
            # Форк с готовой стоимостью не доходит до расчёта; имя оставляем,
            # чтобы в выводе команды было видно, кто именно отклонён.
            who = (item.get("label") or f"#{i}") if isinstance(item, dict) else f"#{i}"
            problems += tuple(Problem(p.code, f"форк '{who}': {p.message}")
                              for p in declared)
            continue
        made, more = _fork(item, i)
        problems += more
        if made is not None:
            out.append(made)
    return tuple(out), problems


def _json_answer(answer):
    """JSON из ответа: fenced-блок, иначе внешние скобки. Не JSON — отказ."""
    text = (answer or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    raw = fenced.group(1) if fenced else text
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None, (Problem("answer", "ответ не JSON — разобрать нечего"),)
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError as e:
        return None, (Problem("answer", f"ответ не читается как JSON: {e}"),)
    if not isinstance(data, dict):
        return None, (Problem("answer", "ответ — не объект: ждёт ключ 'forks'"),)
    return data, ()


def _bounds(data):
    if not isinstance(data, dict):
        return ()
    return tuple(Problem("bounds", f"объявлена граница стоимости '{key}' — "
                                   f"границы считает код из предпосылок, "
                                   f"модель объявляет предпосылки")
                 for key in VALUE_BOUND_KEYS if key in data)


def _fork(item, index):
    if not isinstance(item, dict):
        return None, (Problem("fork", f"форк #{index} — не объект"),)
    conditions = item.get("must_be_true") or []
    if not isinstance(conditions, (list, tuple)):
        conditions = ()
    overrides = item.get("overrides") or {}
    if not isinstance(overrides, dict):
        return None, (Problem("override", f"форк #{index}: overrides — не объект"),)
    out = {}
    problems = []
    for field in ("label", "channel_reason"):
        if field not in item:
            problems.append(Problem(
                "fork", f"форк #{index}: отсутствует обязательное поле '{field}'"))
    for name, raw in overrides.items():
        if not isinstance(raw, dict):
            return None, (Problem(
                "override", f"форк #{index}: переопределение '{name}' — не "
                            f"объект со value, rationale, anchor_class, "
                            f"confirms, source"),)
        for field in ("anchor_class", "confirms"):
            if field not in raw:
                problems.append(Problem(
                    "override", f"форк #{index}: переопределение '{name}': "
                    f"отсутствует обязательное поле '{field}'"))
        out[name] = Override(value=raw.get("value"),
                             rationale=(raw.get("rationale") or "").strip(),
                             anchor_class=raw.get("anchor_class") or "",
                             source=(raw.get("source") or "").strip(),
                             confirms=raw.get("confirms") or "")
    try:
        horizon = int(item.get("horizon_months") or 0)
    except (TypeError, ValueError):
        horizon = 0
    return Fork(label=item.get("label") or "",
                thesis=(item.get("thesis") or "").strip(),
                channel=item.get("channel") or "",
                channel_reason=(item.get("channel_reason") or "").strip(),
                horizon_months=horizon,
                must_be_true=tuple(str(c).strip() for c in conditions
                                   if str(c).strip()),
                overrides=out), tuple(problems)


# ── структурные проверки ─────────────────────────────────────────────────────

def validate(base, fork):
    """Структурные отказы. Пустой список — форк можно считать."""
    out = []
    if fork.label not in LABELS:
        out.append(f"метка '{fork.label}' — форков два: bull и bear "
                   "(base-сценарий это и есть базовая линия)")
    if not fork.thesis.strip():
        out.append("тезис пуст — форк без названного события не объявляется")
    if not fork.must_be_true:
        out.append("must_be_true пуст — у ставки нет проверяемых условий")
    if not fork.overrides:
        out.append("нет ни одного переопределения — форк не отличается от "
                   "базовой линии")
    if fork.horizon_months <= 0:
        out.append("horizon_months не объявлен — нечего переносить и не от чего "
                   "считать годовую доходность")

    _channel(base, fork, out)
    _anchors(fork, out)
    _terminal(fork, out)
    return out


FRACTION_FIELDS = (DISCOUNT_RATE, TERMINAL_GROWTH, TERMINAL_FCF_MARGIN,
                   REQUIRED_YIELD)
MULTIPLIER_FIELDS = (TERMINAL_FCF_MULTIPLE, PE_LOW, PE_HIGH)


def validate_assumption_units(assumptions):
    """Preflight declared baseline units before a paid generator call."""
    if not isinstance(assumptions, dict):
        return ("предпосылки — не JSON-объект",)
    out = []
    owner = assumptions.get("core", assumptions)
    scenarios = owner.get("scenarios", {}) if isinstance(owner, dict) else {}
    if isinstance(scenarios, dict):
        for label, scenario in scenarios.items():
            if not isinstance(scenario, dict):
                continue
            for name in FRACTION_FIELDS:
                if name in scenario:
                    value = scenario[name]
                    if not _number(value) or not 0 <= value < 1:
                        out.append(f"baseline.{label}.{name} вне [0, 1): "
                                   f"{value!r}")
            for name in MULTIPLIER_FIELDS:
                if name in scenario:
                    value = scenario[name]
                    if not _number(value) or not 1 <= value <= 100:
                        out.append(f"baseline.{label}.{name} вне [1, 100]: "
                                   f"{value!r}")
    stakes = assumptions.get(STAKES)
    if isinstance(stakes, list):
        _stake_ranges(stakes, out)
    return tuple(out)


def _ranges(base, branch, out):
    """Reject unit/schema mistakes before trusted valuation arithmetic runs."""
    baseline = _baseline_values(base)
    for name, override in branch.overrides.items():
        value = override.value
        if name in FRACTION_FIELDS and (not _number(value) or not 0 <= value < 1):
            out.append(f"'{name}' — доля должна быть в диапазоне [0, 1), "
                       f"объявлено {value!r}")
        if (name in MULTIPLIER_FIELDS
                and (not _number(value) or not 1 <= value <= 100)):
            out.append(f"'{name}' — мультипликатор должен быть в диапазоне "
                       f"[1, 100], объявлено {value!r}")
        if name == STAKES:
            _stake_ranges(value, out)
        if name in baseline:
            _same_magnitude(name, value, baseline[name], out)


def _baseline_values(base):
    data = base.assumptions
    if not isinstance(data, dict):
        return {}
    owner = data.get("core", data)
    scenarios = owner.get("scenarios", {}) if isinstance(owner, dict) else {}
    values = scenarios.get("base", {}) if isinstance(scenarios, dict) else {}
    return values if isinstance(values, dict) else {}


def _stake_ranges(value, out):
    if not isinstance(value, list):
        return
    for index, stake in enumerate(value):
        ownership = stake.get("ownership_pct") if isinstance(stake, dict) else None
        if not _number(ownership) or not 0 <= ownership < 1:
            out.append(f"'stakes[{index}].ownership_pct' — доля должна быть "
                       f"в диапазоне [0, 1), объявлено {ownership!r}")


def _same_magnitude(name, value, baseline, out):
    pairs = (zip(value, baseline)
             if (isinstance(value, (list, tuple))
                 and isinstance(baseline, (list, tuple)))
             else ((value, baseline),))
    for current, original in pairs:
        if not (_number(current) and _number(original) and current and original):
            continue
        ratio = abs(current / original)
        if ratio < 0.1 or ratio > 10:
            out.append(f"'{name}' отличается от baseline больше чем на "
                       f"один порядок: {current!r} против {original!r}")
            return


def validate_generated(base, branches, parse_problems=()):
    """Validate a complete generated answer before accepting any branch."""
    out = [problem.message if isinstance(problem, Problem) else str(problem)
           for problem in parse_problems]
    if not branches and not out:
        out.append("ответ не содержит форков")
    labels = [branch.label for branch in branches]
    if len(labels) != len(LABELS) or set(labels) != set(LABELS):
        out.append("ответ должен содержать ровно по одному bull и bear")
    for branch in branches:
        errors = validate(base, branch)
        _ranges(base, branch, errors)
        out.extend(f"{branch.label or '?'}: {error}" for error in errors)
        if errors:
            continue
        corridor, calculation_errors = _corridor(base, branch)
        out.extend(f"{branch.label}: {error}" for error in calculation_errors)
        if corridor and not calculation_errors:
            for warning in check_direction(branch, base.corridor, corridor):
                out.append(f"{branch.label} vs baseline: {warning}")
    return tuple(out)


def _channel(base, fork, out):
    """Канал объявлен, обоснован и меряет то, что переопределяет тезис."""
    if fork.channel not in CHANNELS:
        out.append(f"канал '{fork.channel}' не объявлен или неизвестен — "
                   f"каналов три: {', '.join(CHANNELS)}")
    else:
        if not fork.channel_reason.strip():
            out.append(f"канал '{fork.channel}' без основания: выбор канала — "
                       f"часть работы модели, он обязан мерить то, что меняется "
                       f"по тезису")
        if fork.channel == CASH_FLOW and not base.has_flow:
            out.append(f"канал '{CASH_FLOW}' у меры {base.measure} не считается: "
                       f"у базовой линии нет предпосылок потока, FCF для банка "
                       f"не определён обычным образом — тезис идёт через "
                       f"'{EARNINGS}' или '{DIVIDEND}'")
        if base.measure == profile.SOTP and fork.channel == EARNINGS:
            out.append("канал 'прибыль' для SOTP запрещён: EPS × PE не видит "
                       "стейки и cash; используйте канал 'поток' и "
                       "пересчитайте полный SOTP")
        if fork.channel == DIVIDEND and REQUIRED_YIELD not in fork.overrides:
            out.append(f"каналу '{DIVIDEND}' нужна требуемая дивдоходность "
                       f"('{REQUIRED_YIELD}') — наследовать её неоткуда")
        if (fork.channel == DIVIDEND and DPS_FORWARD not in fork.overrides
                and not base.dps):
            out.append(f"каналу '{DIVIDEND}' нечем считаться: ни форк, ни "
                       f"базовая линия не объявили дивиденд")

    for name in fork.overrides:
        owner = _FIELD_CHANNEL.get(name)
        if owner is not None and owner != fork.channel:
            out.append(f"'{name}' — предпосылка канала '{owner}', а объявлен "
                       f"канал '{fork.channel}': канал обязан мерить то, что "
                       f"меняется по тезису")
        if name == STAKES and base.measure != profile.SOTP:
            out.append(f"'{STAKES}' переопределяется только мерой SOTP, а "
                       f"базовая мера — {base.measure}")
        if (name in (TERMINAL_FCF_MARGIN, TERMINAL_FCF_MULTIPLE)
                and base.measure not in (profile.EV_REVENUE, profile.SOTP)):
            out.append(f"'{name}' относится к EV/Revenue core, а базовая "
                       f"мера — {base.measure}")

    present = set(fork.overrides)
    if fork.channel == EARNINGS and present != set(PE_FIELDS):
        missing = [name for name in PE_FIELDS if name not in present]
        out.append(f"поля канала '{EARNINGS}' переопределяются вместе: не "
                   f"хватает {', '.join(missing)}")


def _anchors(fork, out):
    for name, ov in fork.overrides.items():
        if name not in OVERRIDABLE:
            out.append(f"'{name}' не переопределяется: доступны "
                       f"{', '.join(OVERRIDABLE)}")
            continue
        if ov.anchor_class not in ANCHOR_CLASSES:
            out.append(f"'{name}': класс якоря '{ov.anchor_class}' неизвестен")
        if not ov.rationale.strip():
            out.append(f"'{name}': переопределение без обоснования")
        if ov.anchor_class in ANCHORED and not ov.source.strip():
            out.append(f"'{name}': класс якоря '{ov.anchor_class}' требует "
                       f"источника — чей это источник")
        if ov.confirms not in CONFIRMS:
            out.append(f"'{name}': confirms='{ov.confirms}' — должно быть "
                       f"{' или '.join(CONFIRMS)}")
        if ov.anchor_class == ANALYST_JUDGEMENT and ov.confirms != ESTIMATE:
            out.append(f"'{name}': класс '{ANALYST_JUDGEMENT}' означает, что "
                       f"источника нет — тогда confirms может быть только "
                       f"'{ESTIMATE}', а объявлено '{ov.confirms}'")
        if ov.anchor_class in ANCHORED and ov.confirms == ESTIMATE:
            out.append(f"'{name}': класс '{ov.anchor_class}' называет "
                       f"источник, а confirms='{ESTIMATE}' объявляет, что "
                       f"источника нет — одно из двух неверно")


def _terminal(fork, out):
    """Долгосрочный потолок терминала: выше — только под макро-якорем."""
    term = fork.overrides.get(TERMINAL_GROWTH)
    ceiling = measures.TERMINAL_GROWTH_CEILING
    if term is None or not _number(term.value):
        return
    if term.value > ceiling and term.anchor_class != MACRO_GUIDANCE:
        out.append(f"terminal_growth {term.value:.1%} выше потолка "
                   f"{ceiling:.1%} — нужен якорь macro_guidance (ВВП, "
                   f"гайденс регулятора), а объявлен '{term.anchor_class}'")


# ── пересчёт коридора тем же доверенным кодом ────────────────────────────────

def evaluate(base, fork, peer_band=None, *, run_at=None):
    """Коридор форка из его предпосылок и всё, что видно о нём структурно.

    peer_band — PeerBand из agent.peers: полоса сопоставимых как якорь класса
    peer_stats. Модель ссылается на неё вместо собственной прикидки.
    """
    refusals = tuple(validate(base, fork))
    if refusals:
        result = Result(fork=fork, ticker=base.ticker, measure=base.measure,
                        channel=fork.channel, refusals=refusals,
                        peer_band=peer_band)
    else:
        corridor, errors = _corridor(base, fork)
        if errors:
            result = Result(fork=fork, ticker=base.ticker, measure=base.measure,
                            channel=fork.channel, refusals=errors,
                            peer_band=peer_band)
        else:
            result = Result(
                fork=fork, ticker=base.ticker, measure=base.measure,
                channel=fork.channel, corridor=corridor,
                unanchored=tuple(unanchored(fork)),
                estimated=tuple(estimated_band(fork)),
                derived=tuple(derived(fork)),
                warnings=tuple(check_direction(fork, base.corridor, corridor)),
                bettable=is_bettable(fork),
                expected=expected_return(base.price, corridor, fork.horizon_months),
                peer_band=peer_band)
    status = ("refused" if result.is_refusal() else
              "calculated" if result.bettable else "not_bettable")
    store_client.record_fork(
        result.ticker, result.measure, fork.label, result.corridor,
        dataclasses.asdict(fork), status, run_at=run_at)
    return result


def _corridor(base, fork):
    """Коридор объявленного канала. Отказ — когда каналу нечем считаться."""
    if fork.channel == CASH_FLOW:
        return _flow(base, fork)
    if fork.channel == EARNINGS:
        return _earnings(base, fork)
    override = fork.overrides.get(DPS_FORWARD)
    if override is not None:
        dps, fx = override.value, base.fx    # объявлено в валюте отчётности — переводит код
    else:
        dps, fx = base.dps, 1.0              # унаследовано уже в валюте котировки
    required = fork.overrides[REQUIRED_YIELD].value
    if not _positive(dps):
        return None, (f"каналу '{DIVIDEND}' нечем считаться: дивиденда нет ни "
                      f"в форке, ни в базовой линии",)
    if not _positive(required):
        return None, (f"каналу '{DIVIDEND}' нужна положительная требуемая "
                      f"дивдоходность, объявлено {required!r}",)
    level = round(dps * fx / required, 2)
    return (level, level), ()


def _earnings(base, fork):
    """Прибыль на акцию вперёд × полоса мультипликатора.

    База прибыли положительная по определению канала: у убыточного года
    мультипликатора нет, и такой тезис считается каналом потока.
    """
    eps = fork.overrides[EPS_FORWARD].value
    band = [fork.overrides[PE_LOW].value, fork.overrides[PE_HIGH].value]
    if not _positive(eps) or not all(_positive(x) for x in band):
        return None, (f"каналу '{EARNINGS}' нужна положительная прибыль на "
                      f"акцию и положительная полоса мультипликатора: "
                      f"eps_forward = {eps!r}, полоса "
                      f"{band[0]!r}–{band[1]!r}",)
    return _quote((eps * min(band), eps * max(band)), base), ()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _positive(value):
    return _number(value) and value > 0



def _flow(base, fork):
    """Коридор канала потока — тем же кодом, что считала базовая линия.

    Предпосылки форка подставляются в тот же набор объявлений, и расчёт идёт
    через публичный вход слоя мер: новой арифметики не появляется, потому что
    её здесь просто нет. Курс не применяется — слой мер возвращает коридор в
    тех же единицах, в каких его считала базовая линия.
    """
    data = copy.deepcopy(base.assumptions)
    owner = data.get("core", data)          # у холдинга предпосылки живут в core
    scenario = owner.setdefault("scenarios", {}).setdefault("base", {})
    for name in FLOW_FIELDS:
        if name in fork.overrides:
            scenario[name] = fork.overrides[name].value
    if STAKES in fork.overrides:
        data[STAKES] = fork.overrides[STAKES].value
    res = measures.calculate(base.ticker, data)
    if res.is_refusal():
        return None, tuple(f"предпосылки форка не считаются: {e}"
                           for e in res.errors)
    return res.corridor, ()


def _quote(values, base):
    """Валюту котировки считает код, а не модель (D20).

    Курс — тот же, каким переводила базовая линия. У мер из объявленных
    предпосылок слой 1 курс не носит: их коридор и их предпосылки в одних
    единицах, переводить нечего, поэтому здесь курс единица.
    """
    ordered = sorted(round(v * base.fx, 2) for v in values)
    return ordered[0], ordered[-1]


# ── якорь: адресное требование ───────────────────────────────────────────────

def unanchored(fork):
    """Переопределения на одном суждении — форк допустим, но это видно."""
    return [name for name, ov in fork.overrides.items()
            if ov.anchor_class == ANALYST_JUDGEMENT]


def derived(fork):
    """Числа, выведенные аналитиком из подтверждённого драйвера.

    Такое переопределение — законная ставка: событие подтверждено источником,
    но происхождение числа должно быть видно, а не спрятано под классом якоря.
    """
    return [name for name, ov in fork.overrides.items()
            if ov.confirms == DRIVER and ov.anchor_class in ANCHORED]


def has_fact_anchor(override):
    """Does an override inherit a valid anchor from a reported fact?

    In particular, a bear EPS estimated by the analyst remains anchored when
    its risk driver comes from company reporting: ``company_guide/driver``.
    Checking the positive contract here keeps unknown or judgement classes
    from becoming bettable merely because they are not in ``unanchored``.
    """
    return (override.anchor_class in ANCHORED
            and override.confirms in (NUMBER, DRIVER)
            and bool(override.source.strip()))


def estimated_band(fork):
    """Границы оценки, выбранные без источника, — коридор шире, чем доказан."""
    return [name for name in unanchored(fork) if name in BAND_FIELDS]


def is_bettable(fork):
    """Может ли форк стать основанием инвест-идеи.

    Требование к якорю адресное: величина, которую двигает тезис, обязана быть
    подпёрта источником — и такая величина должна быть. Форк, трогающий одни
    полосы оценки, тезис о будущем не несёт: он двигает оценку, а не компанию.
    Полосы могут остаться суждением — это видно через ``estimated_band``, но
    ставку не отменяет.
    """
    carried = [ov for name, ov in fork.overrides.items()
               if name in DRIVING_FIELDS]
    return bool(carried) and all(has_fact_anchor(ov) for ov in carried)


# ── направление, риск и доля оплаченного ─────────────────────────────────────

def check_direction(fork, baseline_corridor, fork_corridor):
    """Совпадает ли знак форка с его меткой.

    Поймано на DELL 24.08: модель подпёрла полосу P/E сопоставимыми (SMCI
    6.96, HPE 12.81) и получила bull-коридор ниже базовой линии — механически
    взятая peer-полоса занижает бумагу, которая торгуется к сопоставимым с
    премией. Это не повод отменять якорь, это повод потребовать объяснить
    премию: либо она структурна и объявлена, либо бумага переоценена.

    Сравниваются СЕРЕДИНЫ, не границы: коридоры почти всегда частично
    перекрываются, и по границам дефект не ловить (у того же DELL верх форка
    выше низа базовой линии, а середина ниже на четверть).
    """
    if not fork_corridor or not baseline_corridor:
        return []
    f_mid, b_mid = _mid(fork_corridor), _mid(baseline_corridor)
    if fork.label == BULL and f_mid < b_mid:
        return [f"bull-форк (середина {f_mid:,.2f}) ниже базовой линии "
                f"(середина {b_mid:,.2f}) на {1 - f_mid / b_mid:.0%}: либо "
                f"метка неверна, либо полоса мультипликатора занижает — "
                f"премия к сопоставимым не объявлена"]
    if fork.label == BEAR and f_mid > b_mid:
        return [f"bear-форк (середина {f_mid:,.2f}) выше базовой линии "
                f"(середина {b_mid:,.2f}): либо метка неверна, либо базовая "
                f"линия занижает"]
    return []


def defer(fork, added_months):
    """Тезис не сломался, а сдвинулся вправо.

    Так аналитик выражает риск: не повышением ставки дисконтирования, а
    переносом года реализации. Коридор тот же — пересчитывать нечего, —
    горизонт растёт, годовая доходность падает.
    """
    return Fork(label=fork.label, thesis=fork.thesis, channel=fork.channel,
                channel_reason=fork.channel_reason,
                horizon_months=fork.horizon_months + added_months,
                must_be_true=fork.must_be_true, overrides=fork.overrides,
                deferred_from=fork.horizon_months)


def expected_return(price, corridor, horizon_months):
    """Доходность от цены к границам коридора за срок форка."""
    years = horizon_months / 12

    def ann(target):
        total = target / price - 1
        return round(total, 6), round((1 + total) ** (1 / years) - 1, 6)

    return {"low": ann(corridor[0]), "high": ann(corridor[1]),
            "mid": ann(_mid(corridor)), "years": years}


def paid_in(price, baseline_corridor, fork_corridor):
    """Какую долю форк-тезиса рынок уже оплатил.

    0 — цена стоит на базовой линии, 1 — цена целиком в тезисе. Доля считается
    вдоль направления форка: bear-тезис оплачивается падением, поэтому цена,
    не дошедшая до середины базовой линии, не оплатила ничего. Отрицательного
    числа не бывает по построению: цена за серединой базовой линии — тезис не
    оплачен, а не «минус четверть тезиса».
    """
    b_mid, f_mid = _mid(baseline_corridor), _mid(fork_corridor)
    if f_mid == b_mid:
        return Paid(None, "форк не сдвигает середину базовой линии — доли нет")
    share = (price - b_mid) / (f_mid - b_mid)
    if share < 0:
        if price < b_mid:
            return Paid(0.0, f"тезис не оплачен: цена ниже середины базовой "
                             f"линии на {1 - price / b_mid:.0%}")
        return Paid(0.0, f"тезис не оплачен: цена выше середины базовой линии "
                         f"на {price / b_mid - 1:.0%}")
    share = round(share, 4)
    if share > 1:
        return Paid(share, f"рынок оплатил тезис целиком и сверх ({share:.0%})")
    return Paid(share, f"рынок оплатил {share:.0%} тезиса")


def _mid(corridor):
    return (corridor[0] + corridor[1]) / 2
