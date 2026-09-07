"""Слой 1 v2 — базовая линия (SPC-008).

Оценка мерой, назначенной слоем профиля: слой не выбирает меру сам и не
перебирает допущения. Для банка мера `ddm_ri` — триангуляция двух независимых
оценок при ОДНОМ наборе допущений: дисконтированный поток дивидендов (Гордон)
и справедливый P/Bv = (ROE − g)/(r − g) при данном ROE. Коридор образует
расхождение этих двух мер, а не сетка ставок и ростов: сошлись — оценка
устойчива, разошлись — ширина коридора честно это показывает.

Дивиденд берётся объявленный вперёд, а не выплаченный за прошлые 12 месяцев:
автоисточник отдаёт trailing, а компания уже объявила вперёд — на KSPI подмена
8.72 на 5.74 USD роняет DDM с 118.77 до 78.21. Ставка и рост объявляет
вызывающий, с обоснованием происхождения: слой не держит их в константах,
иначе их никто никогда не обсуждает. Обе в валюте отчётности (D20), обе меры
считаются в ней же, а готовое значение на акцию переводит код по объявленному
курсу — иначе ставка теряла бы девальвацию.

Расчёту предшествует когерентность (agent.coherence): несходящиеся факты
останавливают слой до числа, поэтому отказ не несёт ни коридора, ни деталей.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import (coherence, freshness, measure_contract, measures, prisms,
               profile, scope)
from .kernel import store_client

# Меры, которые считает этот слой, перечислены в profile.IMPLEMENTED — один
# реестр для профиля и CLI: расхождение реестров печатает ложное «мера ждёт
# слой базовой линии» о мере, которая уже считается.

# Две независимые меры дают устойчивую оценку, пока расходятся меньше чем на
# это. Порог — суждение; объявлено, чтобы его можно было спорить, а не прятать
# в сравнении.
CONVERGENCE_TOLERANCE = 0.05

# Что мере банка нужно сверх универсальных фактов: объявленный вперёд
# дивиденд. Выплаченный за прошлые 12 месяцев — другая величина, и подмена
# молча режет оценку.
_BANK_REQUIREMENTS = (coherence.Requirement(
    "dps_declared", "дивиденд, объявленный компанией вперёд",
    "объявленный дивиденд из отчёта или гайда; выплаченный за прошлые 12 "
    "месяцев — другая величина: на KSPI 5.74 против 8.72 USD, около половины "
    "оценки"),)
_REQUIREMENTS = {profile.DDM_RI: _BANK_REQUIREMENTS}


@dataclass(frozen=True)
class Detail:
    """Обе меры и допущения, из которых они посчитаны.

    Это контракт, который слой 2 (форк) наследует: переопределяется подмножество
    этих полей, а не готовый коридор.
    """
    measures: tuple                # что вошло в триангуляцию: ("ddm", "pbv")
    r: float                       # ставка, номинальная в валюте отчётности
    g: float
    r_why: str
    g_why: str
    dps: float                     # объявленный вперёд, в валюте котировки
    dps_why: str
    dps_trailing: float | None     # выплаченный за 12 месяцев — не он в расчёте
    roe: float                     # прибыль / капитал одного периода
    pbv_multiple: float            # (ROE − g) / (r − g)
    book_value_per_share: float    # валюта котировки
    ddm: float                     # обе меры — в валюте котировки
    pbv: float
    fx: float
    financial_currency: str
    price_currency: str


@dataclass(frozen=True)
class Result:
    """Исход слоя: либо коридор с объявлениями, либо отказ без единого числа."""
    ticker: str
    measure: str
    measure_reason: str
    price: float
    price_currency: str
    mismatches: tuple = ()               # пусто → расчёт состоялся
    corridor: tuple | None = None        # (низ, верх) в валюте котировки
    divergence: float | None = None      # (верх − низ) / середина двух мер
    converged: bool | None = None        # меры сошлись в пределах порога
    verdict: str | None = None           # цена против коридора
    detail: Detail | None = None

    def is_refusal(self):
        return bool(self.mismatches)


def build(ticker, facts=None, rate=None, growth=None, rate_why="",
          growth_why="", *, assumptions=None, physical=None, run_at=None,
          persist=True, _has_info=None):
    """Базовая линия мерой из профиля. Отказ вместо числа, если факты не сошлись.

    Ставка и рост объявляются здесь, в аргументах, вместе с происхождением —
    вызывающий не может посчитать, не объявив их. Профиль берётся своим
    публичным входом (profile.build): слой меру не выбирает и не переписывает.

    `persist=False` — когда базовую линию строит не сам слой baseline, а
    вышестоящий слой (форк/ингест/речек) для якоря: результат нужен, но
    писать его в канон не надо, иначе плодится дубль baseline-строки.
    """
    def _record(result, assumptions, run_at):
        if persist:
            _record_baseline(result, _with_detail(result, assumptions), run_at)
    prof = profile.build(ticker)
    scope_message = scope.gate(prof)
    if scope_message:
        result = _refusal(ticker, prof, facts, [coherence.Mismatch(
            "scope", scope_message,
            "set scope с мерой и обоснованием → confirm")])
        recorded_assumptions = (assumptions if assumptions is not None else
                                _bank_assumptions(
                                    rate, growth, rate_why, growth_why))
        _record(result, recorded_assumptions, run_at)
        return result
    materials = store_client.materials_for_contract(ticker, prof.measure)
    contract_message = measure_contract.gate(vars(prof), materials)
    if contract_message:
        return _refusal(ticker, prof, facts, [coherence.Mismatch(
            "measure_contract", contract_message,
            f"закройте недостающие узлы ресёрча для меры {prof.measure}")])
    if _has_info is None:
        has_info = facts is not None or bool(store_client.get_material(ticker))
    else:
        # The assumptions CLI already ran this same gate with its optional
        # facts file, which is deliberately not part of the measure input.
        has_info = bool(_has_info)
    prism_message = prisms.gate(vars(prof), has_info)
    if prism_message:
        return _refusal(ticker, prof, facts, [coherence.Mismatch(
            "prisms", prism_message,
            "соберите факты/материал и обоснуйте меру через "
            "cli prisms → set scope → confirm")])
    # Non-bank measures have a different fact contract: their model-facing
    # input is a set of declared assumptions, and trusted code derives the
    # bounds.  Keeping this dispatch here preserves one public entry per layer.
    if prof.measure != profile.DDM_RI and assumptions is not None:
        result = measures.calculate(ticker, assumptions, physical=physical)
        _record(result, assumptions, run_at)
        return result
    mismatches = []
    # Отказ на границе слоя, а не AttributeError в глубине когерентности:
    # без фактов проверять нечего любой мере (D21 — отказ это результат).
    if facts is None:
        result = _refusal(ticker, prof, None, [coherence.Mismatch(
            "input", "факты бумаги не переданы",
            "передайте facts отчёта — слой не достаёт их сам")])
        _record(result, assumptions, run_at)
        return result
    if prof.measure != profile.DDM_RI:
        mismatches.append(coherence.Mismatch(
            "measure",
            f"мера {prof.measure} назначена профилем; для неё нужны объявленные "
            f"предпосылки (аргумент assumptions), а не банковские rate/growth",
            f"передайте assumptions для меры {prof.measure}; смена меры в "
            f"профиле требует подтверждения человека"))
    if prof.measure == profile.DDM_RI and (rate is None or growth is None):
        # Значения по умолчанию здесь — незаданные допущения, а не нули:
        # сравнение ставки с ростом иначе падает TypeError вместо отказа.
        result = _refusal(ticker, prof, facts, [coherence.Mismatch(
            "assumptions", "ставка или рост не объявлены",
            "объявите rate и growth с обоснованием — слой не угадывает "
            "допущения (D20)")])
        _record(result, _bank_assumptions(rate, growth, rate_why, growth_why), run_at)
        return result
    if prof.measure == profile.DDM_RI:
        freshness_mismatch = _bank_freshness_mismatch(ticker, facts)
        if freshness_mismatch:
            mismatches.append(freshness_mismatch)
    mismatches += coherence.check(facts, _REQUIREMENTS.get(prof.measure, ()))
    if mismatches:
        result = _refusal(ticker, prof, facts, mismatches)
    else:
        result = _bank(ticker, prof, facts, rate, growth, rate_why, growth_why)
    _record(result, _bank_assumptions(rate, growth, rate_why, growth_why), run_at)
    return result


def _bank_freshness_mismatch(ticker, facts):
    """Check report freshness at the impure baseline boundary."""
    message = freshness.gate(
        facts.price_context, ticker, store_client.get_material)
    if message is None:
        return None
    return coherence.Mismatch(
        "freshness",
        message,
        ("собери collect_facts" if "не проверить" in message else
         "сначала ingest свежего отчёта, потом оценка"),
    )


def _bank_assumptions(rate, growth, rate_why, growth_why):
    return {"rate": rate, "growth": growth, "rate_why": rate_why,
            "growth_why": growth_why}


def _with_detail(result, assumptions):
    """Дозаписать расчётный detail банка в сохраняемые assumptions.

    DCF-меры уже несут все входы в assumptions (fcf/path/rate/terminal) —
    коридор из них выводим. Банк считается на фактах отчёта, которых в
    assumptions нет: сохраняем снимок расчёта (DDM/P-Bv/ROE), чтобы
    детализацию можно было показать позже, не пересчитывая.
    """
    d = getattr(result, "detail", None)
    if d is None or not hasattr(d, "ddm"):
        return assumptions
    enriched = dict(assumptions or {})
    enriched["detail"] = {
        "ddm": d.ddm, "pbv": d.pbv, "roe": d.roe,
        "pbv_multiple": d.pbv_multiple, "dps": d.dps,
        "r": d.r, "g": d.g,
        "book_value_per_share": d.book_value_per_share,
    }
    return enriched


def _record_baseline(result, assumptions, run_at):
    store_client.record_baseline(
        result.ticker, result.measure, result.corridor, assumptions or {},
        "refused" if result.is_refusal() else "calculated", run_at=run_at)


def _bank(ticker, prof, facts, rate, growth, rate_why, growth_why):
    """Банк: DDM и справедливый P/Bv при одном наборе допущений."""
    roe = facts.net_income / facts.book_value
    undefined = _assumptions_undefined(roe, rate, growth)
    if undefined:
        return _refusal(ticker, prof, facts, [undefined])

    fx = 1.0 if facts.fx is None else facts.fx
    # Обе меры — в валюте отчётности, как и ставка: Gordon линеен по числам,
    # поэтому конвертация готового значения на акцию не меняет арифметику,
    # но оставляет ставку номинальной в той же валюте, что и прибыль (D20).
    ddm = facts.dps_declared * (1 + growth) / (rate - growth)
    pbv_multiple = (roe - growth) / (rate - growth)
    pbv = pbv_multiple * facts.book_value / facts.shares

    ddm_quote, pbv_quote = round(ddm * fx, 2), round(pbv * fx, 2)
    corridor = tuple(sorted((ddm_quote, pbv_quote)))
    divergence = (corridor[1] - corridor[0]) / (sum(corridor) / 2)
    detail = Detail(
        measures=("ddm", "pbv"),
        r=rate, g=growth, r_why=rate_why, g_why=growth_why,
        dps=round(facts.dps_declared * fx, 2),
        dps_why=facts.dps_declared_source,
        dps_trailing=(round(facts.dps_trailing * fx, 2)
                      if facts.dps_trailing else None),
        roe=round(roe, 4), pbv_multiple=round(pbv_multiple, 2),
        book_value_per_share=round(facts.book_value / facts.shares * fx, 2),
        ddm=ddm_quote, pbv=pbv_quote, fx=fx,
        financial_currency=facts.financial_currency,
        price_currency=facts.price_currency)
    return Result(ticker=ticker, measure=prof.measure,
                  measure_reason=prof.measure_reason, price=facts.price,
                  price_currency=facts.price_currency, corridor=corridor,
                  divergence=round(divergence, 4),
                  converged=divergence <= CONVERGENCE_TOLERANCE,
                  verdict=_verdict(facts.price, corridor), detail=detail)


def _refusal(ticker, prof, facts, mismatches):
    """Отказ: расчёт остановлен до числа — ни коридора, ни деталей."""
    # facts может отсутствовать, если отказ случился до фактов: отказ всё
    # равно не несёт ни коридора, ни деталей, ни цены.
    return Result(ticker=ticker, measure=prof.measure,
                  measure_reason=prof.measure_reason,
                  price=getattr(facts, "price", None),
                  price_currency=getattr(facts, "price_currency", None),
                  mismatches=tuple(mismatches))


def _assumptions_undefined(roe, rate, growth):
    """Мера Гордона определена не при любых допущениях: отказ, а не минус в коридоре."""
    if not (rate > growth):
        return coherence.Mismatch(
            "assumptions",
            f"ставка {rate:.0%} не выше роста {growth:.0%} — модель Гордона "
            f"не определена",
            "объявите номинальную ставку в валюте отчётности выше "
            "долгосрочного роста (D20)")
    if roe <= growth:
        return coherence.Mismatch(
            "assumptions",
            f"ROE {roe:.0%} не выше роста {growth:.0%} — справедливый P/Bv "
            f"отрицателен",
            "проверьте прибыль и капитал одного периода: банк, зарабатывающий "
            "меньше роста, не стоит положительного P/Bv")
    return None


def _verdict(price, corridor):
    """Консервативно в обе стороны: «дешевле» — от нижней границы, «дороже» — от верхней."""
    low, high = corridor
    if price < low:
        return f"дешевле нижней границы на {low / price - 1:.0%}"
    if price > high:
        return f"дороже верхней границы на {price / high - 1:.0%}"
    return "внутри коридора"
