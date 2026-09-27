#!/usr/bin/env python3
"""Golden behavior for owners' earnings (Tragic Algebra), IV15 and AICT tiering."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import aict, iv15, owners_earnings  # noqa: E402
from agent.measures import _levered_vps  # noqa: E402


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main():
    # --- Tragic Algebra -----------------------------------------------------
    # Чистое разбавление без выкупа: Σ = I×P + C, I = ΔS.
    diluter = owners_earnings.Period(
        2020, net_income=100, gaap_sbc=12, shares_start=1000, shares_end=1020,
        buyback_dollars=0, buyback_shares=0, withholding_net=3, avg_price=1.0)
    check("чистое разбавление: OE = 100 + 12 − 23",
          abs(owners_earnings.owners_earnings(diluter) - 89.0) < 1e-9)

    # Без программы выкупа P=T/W не определена — нужна средняя рыночная цена.
    no_price = owners_earnings.Period(
        2020, 100, 12, 1000, 1020, buyback_dollars=0, buyback_shares=0,
        withholding_net=3)
    try:
        owners_earnings.sbc_cost(no_price)
        raise AssertionError("W=0 без avg_price обязан отказывать")
    except ValueError as exc:
        check("W=0 без avg_price — отказ с указанием, чего не хватает",
              "avg_price" in str(exc))

    # Выкуп сверх антидилютивного: ΔS<0, но I = ΔS + W всё ещё положительна.
    buyer = owners_earnings.Period(
        2021, net_income=200, gaap_sbc=20, shares_start=1000, shares_end=960,
        buyback_dollars=600, buyback_shares=60, withholding_net=5)
    check("выкуп сверх антидилютивного: I = ΔS + W = 20 акций",
          abs(owners_earnings.sbc_cost(buyer) - (20 * 10 + 5)) < 1e-9)

    pooled = owners_earnings.delta_e([diluter, buyer])
    check("ΔE взвешена прибылью, а не усреднена по годам",
          abs(pooled["delta_e"] - (89.0 + 15.0) / 300) < 1e-9)
    check("декада — глубина стандарта: меньше помечается shallow",
          pooled["shallow"] is True)

    # --- IV15 ---------------------------------------------------------------
    path = [0.10] * 5 + [0.07] * 5 + [0.04] * 5
    ddm_only = iv15.calculate(650, path, 0.03, 12, 57, confidence_ddm=1.0)
    check("конец DDM — это levered-мера, прибитая к 15%: расхождения нет",
          abs(ddm_only.per_share - _levered_vps(650, path, 0.15, 0.03, 57)) < 1e-9)

    check("горизонт фиксирован — путь не 15 лет отвергается",
          _refuses(lambda: iv15.calculate(650, [0.05] * 10, 0.03, 12, 57)))
    check("terminal_growth не ниже 15% — отказ, а не бесконечность",
          _refuses(lambda: iv15.calculate(650, path, 0.15, 12, 57)))

    check("мультипликатор выводится: r=10%, g=3% → 14.3×",
          abs(iv15.terminal_multiple(0.10, 0.03) - 1 / 0.07) < 1e-9)
    check("щедрая пара r/g упирается в потолок мультипликатора",
          _refuses(lambda: iv15.terminal_multiple(0.08, 0.05)))

    # Живой репер: PAYC у Бьюри — IV15 $91.38 при цене $134.90 (P/IV15 1.48×),
    # прибыль владельца ~$650M, ~58.6M акций, тир Stone. Проверяем движок
    # инверсией: какой рост прибыли владельца подразумевает опубликованное
    # число. Ответ ~2% в год — при выручке, растущей ~12%. Тир давит допущение
    # о росте, и это его рабочее содержание, а не украшение.
    multiple = iv15.terminal_multiple(0.115, 0.03)
    payc = iv15.calculate(650, [0.02] * 15, 0.03, multiple, 58.6)
    check(f"репер PAYC: рост 2% даёт IV15 {payc.per_share:.2f} ≈ 91.38",
          abs(payc.per_share - 91.38) / 91.38 < 0.02)
    check("репер PAYC: P/IV15 ≈ опубликованных 1.48×",
          abs(134.90 / payc.per_share - 1.48) < 0.03)

    compounder = iv15.calculate(650, path, 0.03, multiple, 58.6)
    check("тот же бизнес на траектории компаундера переоценивается в 1.5+ раза",
          compounder.per_share / payc.per_share > 1.5)

    check("отрицательная прибыль владельца помечается, а не прячется",
          any("не положительна" in w
              for w in iv15.calculate(-50, path, 0.03, 12, 57).warnings))

    # --- Composite ----------------------------------------------------------
    check("P/IV15 = 1.0× — ровно 15% годовых — даёт 100 очков оценки",
          abs(iv15.valuation_score(1.0) - 100) < 1e-9)
    check("отрицательный IV15 — дно шкалы оценки",
          iv15.valuation_score(None) == -50.0)
    check("незнакомый тир — отказ, а не молчаливый ноль",
          _refuses(lambda: iv15.quality_score("granite")))
    pool_note = iv15.composite(owners_earnings.POOL_DELTA_E, "stone", 1.5,
                               delta_e_is_pool=True)["notes"]
    check("ΔE пулом не выдаётся за факт", any("пулом" in n for n in pool_note))

    # --- AICT ---------------------------------------------------------------
    solid = dict(compliance_embedded=True, mission_critical=True,
                 ai_aggressor=True, seat_exposure_large=False,
                 moat_shallow=False, owns_ai=True, ai_at_scale=True,
                 rnd_real=True, seat_based=True, peer_pressure_high=False,
                 switching_costs_high=True, usage_priced=False,
                 acquisition_strategy=True)
    check("исход рядом с ИИ-native ясен → Fortress",
          aict.hypothesis(dict(solid, ai_native_uncertainty=False)) == "fortress")
    check("тот же бизнес при неясном исходе → Castle",
          aict.hypothesis(dict(solid, ai_native_uncertainty=True)) == "castle")
    check("признак не объявлен — гипотезы нет, а не произвольный тир",
          aict.hypothesis(solid) is None)
    check("непроверенный тир виден: неназванные признаки перечислены",
          any("не объявлено" in n for n in aict.notes(solid)))
    check("usage-based не засчитывается как защита от seat-риска",
          any("смазывает" in n for n in aict.notes(
              dict(solid, usage_priced=True, seat_exposure_large=True))))

    print("\nIV15 eval: OK")
    return 0


def _refuses(call):
    try:
        call()
    except ValueError:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
