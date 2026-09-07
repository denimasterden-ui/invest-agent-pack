"""Проверка когерентности фактов — слой 1 v2 (SPC-008).

Считать по рассогласованным данным — значит выдать число, похожее на оценку:
цена в одной валюте, отчётность в другой; акции от автоисточника (basic), а
EPS посчитан на diluted; баланс за прошлый год при доступном квартальном.
Прототип поймал все три случая на живых бумагах: DELL — sharesOutstanding 325M
против diluted 684M, расхождение ровно вдвое; KSPI и 1810.HK — валюта
отчётности против валюты котировки; KSPI — баланс за прошлый год при доступном
квартальном, после исправления сходимость мер выросла вдвое.

Поэтому проверка живёт ДО расчёта и останавливает его. Отказ с указанием,
какое число запросить из отчётности, дешевле числа, посчитанного через две
валюты. Отказ — штатный исход (D21), а не сбой: расчёт дошёл до решения и не
стал считать. Рассогласования собираются все сразу, а не по первому — к
отчётности ходят один раз, а не три.

Слой не знает, откуда пришли факты: автоисточник, материал или человек. Он
знает только, что числа обязаны сходиться между собой. Конвертацию валют
делает код по объявленному курсу (D20), поэтому без курса расчёт не
начинается. Мера говорит, какие факты нужны сверх универсальных: `missing`
сообщает об отсутствующем, вызывающий — где его брать.
"""
import math
import dataclasses
from dataclasses import dataclass, field
from datetime import date

# Сходимость EPS с прибылью: basic против diluted и округление дают расхождение
# в доли процента, а подмена валюты или размерности акций — на десятки.
EPS_TOLERANCE = 0.02

# Один отчётный квартал плюс запас на публикацию. Баланс за прошлый год при
# доступном квартальном — тот случай, который прототип поймал на KSPI.
MAX_PERIOD_LAG_DAYS = 100

# Средний квартал в днях (365.25 / 4): запаздывание периода переводится в
# кварталы для сообщения, а не для решения — решает MAX_PERIOD_LAG_DAYS.
DAYS_PER_QUARTER = 91.3

# Поля факта, без которых объект не собирается. Остальные — объявляются, когда
# есть: нет курса, нет дивиденда — это отказ расчёта, а не ошибка файла.
REQUIRED_FIELDS = ("ticker", "price", "price_currency", "financial_currency",
                   "shares", "shares_source", "net_income", "eps", "book_value",
                   "period_end", "available_end")

_NUMERIC = ("price", "shares", "net_income", "eps", "book_value", "fx",
            "dps_declared", "dps_trailing")


@dataclass(frozen=True)
class Requirement:
    """Число, без которого мера не считается: что это и какое число запросить."""
    field: str
    label: str
    request: str


# Обязательные числа любого расчёта: без них когерентность не проверить.
UNIVERSAL = (
    Requirement("price", "цена", "котировку на дату расчёта"),
    Requirement("shares", "число акций", "число акций из отчёта"),
    Requirement("net_income", "прибыль за период",
                "прибыль за период, кончающийся датой периода отчётности"),
    Requirement("eps", "прибыль на акцию (EPS)",
                "EPS из того же отчёта, что и прибыль"),
    Requirement("book_value", "капитал (book value)",
                "капитал акционеров из баланса"),
)


@dataclass(frozen=True)
class Mismatch:
    """Одно рассогласование: что не сходится и какое число запросить.

    Отказ слоя собирается из них, но сам класс — «отказ», а не «сбой» (D21):
    расчёт дошёл до решения и не стал считать. Поэтому класс не называется
    Failure — глоссарий уже занял это слово за сбоем.
    """
    code: str        # currency | income_eps | period | shares_source | имя поля
    message: str     # что рассогласовано, с числами
    request: str     # какое число запросить из отчётности


@dataclass(frozen=True)
class Facts:
    """Факты одного отчёта: его валютой, в штуках, без «B» и «M» (D20).

    Числа согласованы по построению, если взяты из одного отчёта: прибыль, EPS,
    число акций и капитал одного периода. Прибыль — приведённая к году, иначе
    ROE считается по куску года и завышается во столько же раз. Валюта
    котировки и валюта отчётности объявляются раздельно — их совпадение не
    предполагается (KSPI, 1810.HK). fx — курс отчётность → котировка; не
    объявлен, когда валюта одна.
    """
    ticker: str
    price: float
    price_currency: str
    financial_currency: str
    shares: float
    shares_source: str          # basic / diluted / средневзвешенное — D23
    net_income: float
    eps: float
    book_value: float
    period_end: str             # конец периода отчётности, ГГГГ-ММ-ДД
    available_end: str          # самый свежий период, доступный в источнике
    fx: float = None
    dps_declared: float = None  # объявленный компанией вперёд, не выплаченный
    dps_declared_source: str = ""
    dps_trailing: float = None  # выплаченный за прошлые 12 месяцев — для сравнения
    capital_signals: dict = field(default_factory=dict)  # G: сделки инсайдеров
    price_context: dict = field(default_factory=dict)    # H: контекст котировки
    source: str = ""            # кто принёс факты: автоисточник, материал, человек

    @classmethod
    def from_dict(cls, raw):
        """Факты из файла команды. Опечатка в имени — ошибка, а не тихий пропуск.

        Молчаливо пропущенное поле превратилось бы в «числа не хватает» и
        отправило бы к отчётности за тем, что уже лежит в файле.
        """
        if not isinstance(raw, dict):
            raise ValueError(f"факты ждут объект с полями, пришло "
                             f"{type(raw).__name__}")
        fields = tuple(f.name for f in dataclasses.fields(cls))
        unknown = sorted(set(raw) - set(fields))
        if unknown:
            raise ValueError(f"неизвестные факты: {', '.join(unknown)} — "
                             f"поля фактов: {', '.join(fields)}")
        absent = [n for n in REQUIRED_FIELDS if _empty(raw.get(n))]
        if absent:
            raise ValueError(f"в фактах нет {', '.join(absent)} — без них "
                             f"когерентность не проверить")
        for name, value in raw.items():
            if name in _NUMERIC and value is not None and not _is_number(value):
                raise ValueError(f"{name} ждёт число, пришло {value!r}")
            if (name in ("capital_signals", "price_context")
                    and not isinstance(value, dict)):
                raise ValueError(f"{name} ждёт словарь, пришло {value!r}")
        return cls(**{k: v for k, v in raw.items() if k in fields})


def check(facts, requirements=()):
    """Все рассогласования фактов сразу. Пусто — расчёту можно начинаться.

    requirements — что мере нужно сверх универсального, объектами Requirement.
    """
    mismatches = list(_currency(facts))
    mismatches += missing(facts, tuple(UNIVERSAL) + tuple(requirements))
    mismatches += _shares_source(facts)
    mismatches += _income_eps(facts)
    mismatches += _period(facts)
    return tuple(mismatches)


def missing(facts, requirements):
    """Обязательное число отсутствует или неположительно — тоже рассогласование.

    Код отказа — имя поля: по нему видно, какого числа не хватило, не читая
    текста.
    """
    out = []
    for req in requirements:
        value = getattr(facts, req.field, None)
        if not _is_number(value) or value <= 0:
            out.append(Mismatch(
                req.field, f"нет числа: {req.label} ({req.field} = {value!r})",
                f"запросите из отчётности: {req.request}"))
    return out


def _currency(facts):
    if facts.price_currency == facts.financial_currency:
        if facts.fx not in (None, 1):
            return [Mismatch(
                "currency",
                f"цена и отчётность в одной валюте ({facts.price_currency}), "
                f"а курс объявлен {facts.fx} — конвертация прошла бы дважды",
                "уберите лишний курс или приведите цену и отчётность к одной "
                f"валюте ({facts.price_currency})")]
        return []
    # Курс 1 при разных валютах — тот же расчёт через две валюты: число вышло
    # бы в разы мимо (KZT против USD — в сотни раз).
    if not _is_number(facts.fx) or facts.fx <= 0 or facts.fx == 1:
        return [Mismatch(
            "currency",
            f"цена в {facts.price_currency}, отчётность в "
            f"{facts.financial_currency}, курс "
            f"{'не объявлен' if facts.fx is None else facts.fx} — число вышло "
            f"бы посчитанным через две валюты",
            f"запросите курс {facts.financial_currency}→{facts.price_currency} "
            f"на дату расчёта: конвертацию делает код (D20)")]
    return []


def _shares_source(facts):
    if not (facts.shares_source or "").strip():
        return [Mismatch(
            "shares_source",
            "число акций без размерности: basic, diluted и средневзвешенное "
            "дают расхождение в разы (DELL: 325M против 684M)",
            "объявите, откуда акции — размерность выбирает аналитик, а сверяет "
            "код (D23)")]
    return []


def _income_eps(facts):
    if not all(_is_number(v) and v > 0
               for v in (facts.eps, facts.shares, facts.net_income)):
        return []   # недостающее уже названо: второй раз о том же не говорим
    implied = facts.eps * facts.shares
    gap = abs(implied - facts.net_income) / facts.net_income
    if gap <= EPS_TOLERANCE:
        return []
    return [Mismatch(
        "income_eps",
        f"EPS {facts.eps} × {_num(facts.shares)} акций = {_num(implied)}, "
        f"а прибыль {_num(facts.net_income)} — расходятся на {gap:.0%}",
        "запросите из одного отчёта прибыль и EPS за один и тот же период и "
        "одной размерности акций")]


def _period(facts):
    end, avail = _date(facts.period_end), _date(facts.available_end)
    if end is None or avail is None:
        bad = facts.period_end if end is None else facts.available_end
        return [Mismatch("period",
                         f"конец периода отчётности не распознан: {bad!r}",
                         "укажите даты периодов в формате ГГГГ-ММ-ДД")]
    if end > avail:
        return [Mismatch("period",
                         f"отчётность за {end}, а источник отдаёт до {avail} — "
                         f"факты новее источника",
                         "проверьте, за какой период факты, и запросите "
                         f"отчётность за период до {avail}")]
    lag = (avail - end).days
    if lag <= MAX_PERIOD_LAG_DAYS:
        return []
    return [Mismatch("period",
                     f"отчётность за {end}, а доступно {avail} — база устарела "
                     f"на {round(lag / DAYS_PER_QUARTER)} кв.",
                     f"запросите из отчётности баланс, прибыль и EPS за период "
                     f"до {avail}")]


def _num(value):
    """Число в сообщении: читаемое, без научной записи и без запятых в разрядах."""
    return f"{value:,.0f}".replace(",", " ")


def _date(value):
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _empty(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _is_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))
