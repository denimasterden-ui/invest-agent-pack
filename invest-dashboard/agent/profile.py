"""Слой 0 v2 — профиль бумаги (SPC-008).

Детерминированный обзор: тип бизнеса решает, какой мерой считается базовая
линия, какими физическими драйверами объявляется рост, чем ограничен рост
сверху и с кем сравнивается мультипликатор.

В v1 это частично есть, но разорвано: research_type проставлен в tickers.py,
playbook написан в knowledge/methods/_choosing.md, а выбор меры делает
method_router по знаку FCF, не глядя ни на то, ни на другое — отсюда базовая
линия Kaspi 59.19–67.75 при цене 105.55 и вывод «дорого на +56%» там, где мера
банка даёт «дешевле на 11%». Здесь всё сведено в один объект, из которого слой
базовой линии берёт и меру, и термины, в которых формулируется тезис.
"""
from dataclasses import dataclass, field
import warnings

from .kernel import tickers
from . import profile_store

# Меры оценки. Реестр один — его читает и профиль (measure_implemented), и
# CLI: второй реестр рядом неизбежно расходится и печатает неправду.
LEVERED = "levered"
EV_REVENUE = "ev_revenue"
SOTP = "sotp"
DDM_RI = "ddm_ri"
NAV = "nav"
FFO = "ffo"
IMPLEMENTED = (DDM_RI, LEVERED, EV_REVENUE, SOTP)

# Levered FCF already reflects interest and debt service, so debt must not be
# subtracted from its DCF a second time.  Above this ratio the resulting equity
# corridor is nevertheless too insensitive to refinancing/default risk to be
# read without an explicit warning.
NET_DEBT_TO_FCF_WARNING_THRESHOLD = 8.0

# Поля, которые событие может перекрыть, читаются из журнала (OVERRIDABLE).
# У холдинга обёртка отвечает за меру (сумма частей), природа бизнеса — в core.
WRAPPER_FIELDS = ("business_kind", "measure", "measure_reason",
                  "capital_signals", "price_context", "segments", "scope")


@dataclass(frozen=True)
class Profile:
    ticker: str
    research_type: str
    business_kind: str          # что это за бизнес человеческими словами
    measure: str                # чем считается базовая линия
    measure_reason: str
    drivers: tuple              # физические величины, которыми объявляется рост
    caps: tuple                 # чем рост ограничен сверху
    comps: tuple                # с кем сравнивается мультипликатор
    multiple_metric: str        # в каких единицах сравнение осмысленно
    data_gaps: tuple = ()       # чего нет в автоисточнике
    driver_facts: dict = None   # driver_name → {value, anchor_class, source, confirms, reason}
    capital_signals: dict = field(default_factory=dict)  # G: insider transactions
    price_context: dict = field(default_factory=dict)    # H: ranges, move, volume, report reaction
    segments: tuple = ()            # material business segments
    scope: dict = field(default_factory=dict)  # confirmed valuation-measure choice
    core: object = None         # холдинг: профиль основного бизнеса внутри

    @property
    def effective(self):
        """Профиль, по которому объявляются драйверы.

        У холдинга обёртка задаёт меру (сумма частей), но драйверы и comps
        живут в основном бизнесе: NBIS — это sotp снаружи и аренда мощности
        внутри, и мегаватты нельзя терять из-за наличия долей.
        """
        return self.core or self

    @property
    def measure_implemented(self):
        return self.measure in IMPLEMENTED


# Профили по типу бизнеса. Внутри research_type бывает развилка (банк против
# процессинга, неоклауд против производителя железа) — она решается явным
# списком, а не эвристикой по цифрам: перепутать банк с процессингом дороже,
# чем поддерживать список.
NEOCLOUD = {"NBIS"}
HARDWARE_AI = {"DELL"}
MEMORY_CYCLE = {"MU"}
BANKS = {"KSPI", "TIGR", "MRX", "2318.HK", "SBER", "T"}

_BY_KIND = {
    "bank": dict(
        business_kind="банк / финансовая экосистема",
        measure=DDM_RI,
        measure_reason="выданный кредит — работающий актив, а не отток; "
                       "FCF для банка не определён обычным образом",
        drivers=("ROE", "длительность сверхдоходности", "payout", "book value"),
        caps=("ROE сходится к стоимости капитала",),
        comps=("ITUB", "HDB", "GGAL", "KB"),
        multiple_metric="pbv_per_roe",
        data_gaps=(),
    ),
    "neocloud": dict(
        business_kind="аренда вычислительной мощности",
        measure=EV_REVENUE,
        measure_reason="выручка следует за развёрнутой мощностью; "
                       "FCF отрицателен на capex-цикле",
        drivers=("развёрнутые МВт", "выручка на МВт", "загрузка",
                 "contracted power", "темп ввода ГВт/год"),
        caps=("законтрактованная мощность", "физический темп ввода"),
        comps=("IREN", "CRWV"),
        multiple_metric="ev_per_sales",
        data_gaps=("развёрнутые МВт", "загрузка"),
    ),
    "hardware_ai": dict(
        business_kind="производитель железа с AI-сегментом",
        measure=LEVERED,
        measure_reason="FCF осмыслен и положителен; AI-сегмент — драйвер "
                       "внутри группы, не отдельная мера",
        drivers=("backlog AI-сегмента", "маржа сегмента", "доля сегмента в выручке"),
        caps=("backlog не превращается в выручку быстрее цикла поставок",),
        comps=("HPE", "SMCI"),
        multiple_metric="forward_pe",
        data_gaps=("backlog", "маржа сегмента"),
    ),
    "memory_cycle": dict(
        business_kind="циклический производитель памяти",
        measure=LEVERED,
        measure_reason="FCF осмыслен, но год пика или дна не база — "
                       "нужна нормализация по циклу",
        drivers=("цена памяти", "загрузка фабрик", "капекс цикла"),
        caps=("исторический размах цикла",),
        comps=("WDC", "STX"),
        multiple_metric="forward_pe",
        data_gaps=("нормализованная прибыль",),
    ),
    "holding": dict(
        business_kind="холдинг с отдельно оцениваемыми долями",
        measure=SOTP,
        measure_reason="доли оцениваются отдельно, иначе прячутся в "
                       "терминальном мультипликаторе core",
        drivers=("оценка каждой доли", "мера core"),
        caps=("потолок меры core",),
        comps=(),
        multiple_metric="sum_of_parts",
        data_gaps=("оценки непубличных долей",),
    ),
    "generic": dict(
        business_kind="обычный операционный бизнес",
        measure=LEVERED,
        measure_reason="FCF положителен и достаточно стабилен, чтобы его "
                       "компаундить",
        drivers=("рост выручки", "маржа FCF"),
        caps=("терминал не выше долгосрочного ВВП",),
        comps=(),
        multiple_metric="forward_pe",
        data_gaps=(),
    ),
}


def _kind(ticker, research_type, has_stakes):
    if has_stakes:
        return "holding"
    if research_type in ("fintech", "russia") and ticker in BANKS:
        return "bank"
    if ticker in NEOCLOUD:
        return "neocloud"
    if ticker in HARDWARE_AI:
        return "hardware_ai"
    if ticker in MEMORY_CYCLE:
        return "memory_cycle"
    return "generic"


def _build_registry(ticker, research_type=None, has_stakes=None, *,
                    overrides=None, replay_local=True):
    """Профиль из типа бизнеса, поверх — накопленное знание из хранилища.

    Константы здесь это seed: то, что известно про тип бизнеса вообще. Всё,
    что узнали про конкретную бумагу, лежит событиями в profile_store и
    перекрывает seed. Неподтверждённая смена меры не перекрывает ничего.

    research_type и has_stakes читаются из tickers.py; явный аргумент нужен,
    чтобы собрать профиль бумаги вне портфеля. Бумаги, которой нет в
    tickers.py, тип бизнеса неоткуда взять — отказ вместо тихого generic.
    """
    entry = tickers.TICKERS_BY_KEY.get(ticker)
    if research_type is None or has_stakes is None:
        if entry is None:
            raise ValueError(
                f"{ticker}: нет в tickers.py — тип бизнеса неоткуда взять; "
                f"передайте research_type явно, если бумага вне портфеля")
        if research_type is None:
            research_type = entry.get("research_type", "default")
        if has_stakes is None:
            has_stakes = bool(entry.get("has_stakes"))

    kind = _kind(ticker, research_type, has_stakes)
    core_fields = None
    if kind == "holding":
        core_fields = dict(_BY_KIND[_kind(ticker, research_type, has_stakes=False)])
    wrapper_fields = dict(_BY_KIND[kind])

    state = dict(overrides) if overrides is not None else {}
    if replay_local and overrides is None:
        state, _pending = profile_store.replay(ticker)
    # Canonical rows contain storage metadata as well as profile fields.
    state = {name: value for name, value in state.items()
             if name in profile_store.OVERRIDABLE}
    driver_facts = state.pop("driver_facts", None) or {}
    for name, value in state.items():
        if name not in profile_store.OVERRIDABLE:
            continue  # поле, ушедшее из профиля, не должно ломать реплей
        # «Драйверы NBIS» — про мегаватты, даже когда мера — сумма частей.
        target = wrapper_fields
        if core_fields is not None and name not in WRAPPER_FIELDS:
            target = core_fields
        target[name] = tuple(value) if isinstance(value, list) else value

    # Закрытые ручным вводом пробелы убираются из data_gaps: драйвер,
    # названный так же, как элемент data_gaps, больше не пробел.
    if driver_facts:
        for target_dict in ([wrapper_fields] + ([core_fields] if core_fields else [])):
            gaps = list(target_dict.get("data_gaps", ()))
            filled = [g for g in gaps if g in driver_facts]
            if filled:
                target_dict["data_gaps"] = tuple(g for g in gaps if g not in driver_facts)

    # Обёртка и core получают собственные копии словаря: frozen-объект не
    # должен делить мутабельное содержимое с другим frozen-объектом.
    core_facts = dict(driver_facts)
    wrapper_facts = dict(driver_facts)
    if core_fields is not None:
        core = Profile(ticker=ticker, research_type=research_type,
                       driver_facts=core_facts, **core_fields)
    else:
        core = None
    built = Profile(ticker=ticker, research_type=research_type, core=core,
                    driver_facts=wrapper_facts, **wrapper_fields)
    if not built.effective.comps:
        warnings.warn(
            f"{ticker}: профиль не содержит comps; полоса мультипликатора "
            "не будет построена — запустите agent-run/collect_peers.py для "
            "подбора sector peers по business_kind",
            RuntimeWarning, stacklevel=2)
    return built


def build(ticker, research_type=None, has_stakes=None):
    """Build a profile from the server canon, or the local registry fallback."""
    from .kernel import store_client

    if not store_client.configured():
        return _build_registry(ticker, research_type, has_stakes)
    canonical = store_client.get_profile(ticker)
    if isinstance(canonical, Profile):
        return canonical
    return _build_registry(
        ticker, research_type, has_stakes,
        overrides=canonical, replay_local=False,
    )


def record_change(ticker, field, new_value, reason, author="model"):
    """Объявить изменение профиля — путь записи для команды и разбора материала.

    Событие несёт старое и новое значение; старое знает только собранный
    профиль, даже когда это seed, а не прошлое событие. Возвращает признак
    того, что изменение уже в силе: смена меры ждёт человека, остальные поля
    применяются сразу.
    """
    current = build(ticker)
    return profile_store.record(ticker, field, new_value, reason, author,
                                old_value=getattr(current, field, None))
