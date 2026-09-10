#!/usr/bin/env python3
"""Offline contract eval for the pure prism helpers (SPC-018/A1)."""
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import prisms  # noqa: E402


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main():
    cases = [
        (
            "hyper-growth",
            {"research_type": "ai_infra", "has_stakes": False},
            {"fcf": [20, 35, 50], "sbc": 60, "net_income": 100,
             "net_cash": 25},
            {"fcf_sign": 1, "fcf_stable": True, "sbc_ratio": 0.6,
             "net_cash_sign": 1, "equity_to_ev": None, "has_stakes": False,
             "research_type": "ai_infra"},
        ),
        (
            "cyclical",
            {"research_type": "mining"},
            {"fcf": [80, -30, 45], "net_cash": -10},
            {"fcf_sign": 1, "fcf_stable": False, "sbc_ratio": None,
             "net_cash_sign": -1, "equity_to_ev": None, "has_stakes": None,
             "research_type": "mining"},
        ),
    ]
    for label, profile, facts, expected in cases:
        check(f"signals: {label}", prisms.signals(profile, facts) == expected)

    entries = prisms.catalog()
    check("catalog is not empty", bool(entries))
    for fragment in ("Marathon", "Quality Investing", "Greenblatt"):
        check(f"catalog contains {fragment}",
              any(fragment in prism.name for prism in entries))
    marathon = next(prism for prism in entries if "Marathon" in prism.name)
    check("catalog fields are parsed",
          bool(marathon.signs and marathon.applies and marathon.offers))

    good = {"scope": {"reason": "Marathon: mid-cycle base",
                       "confirmed_by": "analyst"}}
    no_reason = {"scope": {"confirmed_by": "analyst"}}
    no_confirmation = {"scope": {"reason": "Quality Investing"}}
    check("justified with reason and confirmation",
          prisms.justified(good).measure_justified)
    check("not justified without reason",
          not prisms.justified(no_reason).measure_justified)
    check("not justified without confirmation",
          not prisms.justified(no_confirmation).measure_justified)
    check("justification exposes reason",
          prisms.justified(good).reason == "Marathon: mid-cycle base")

    blind = prisms.gate(good, has_info=False)
    check("gate refuses blind prisms", blind is not None and
          "призмы вслепую" in blind)
    ungrounded = prisms.gate(no_reason, has_info=True)
    check("gate refuses an unjustified measure", ungrounded is not None and
          "cli prisms → выбери меру → set scope → confirm" in ungrounded)
    check("gate passes informed justified measure",
          prisms.gate(good, has_info=True) is None)

    # SPC-020: equity_to_ev signal + measure-fit rubric (дилиберация)
    debt = prisms.signals(
        {"research_type": "telecom_media"},
        {"price": 250, "shares": 140_000_000, "net_debt": 97_000_000_000,
         "fcf": [5, 5.5, 6]})
    check("equity_to_ev computed",
          debt["equity_to_ev"] is not None and abs(debt["equity_to_ev"] - 0.265) < 0.01)
    fit = {m: (za, pr) for m, za, pr in prisms.measure_fit(debt)}
    check("debt-dominated: levered gets ПРОТИВ долг-доминирования",
          any("долг доминирует" in p for p in fit["levered"][1]))
    check("debt-dominated: ev_revenue gets ЗА enterprise",
          any("enterprise" in z for z in fit["ev_revenue"][0]))

    bank = prisms.signals(
        {"research_type": "fintech"},
        {"price": 100, "shares": 190_000_000, "net_debt": 900_000_000_000,
         "fcf": [1, 1, 1]})
    bfit = {m: (za, pr) for m, za, pr in prisms.measure_fit(bank)}
    check("bank: ddm_ri gets ЗА (не выталкивается в ev_revenue вслепую)",
          bool(bfit["ddm_ri"][0]))

    noshare = prisms.signals({"research_type": "consumer"}, {"fcf": [1, 2, 3]})
    check("no shares → equity_to_ev None (пробел, не блок)",
          noshare["equity_to_ev"] is None)
    nfit = {m: (za, pr) for m, za, pr in prisms.measure_fit(noshare)}
    check("no shares → нет EV-производного ПРОТИВ у levered",
          not any(("EV" in p or "долг" in p) for p in nfit["levered"][1]))

    print("All prisms evals passed")


if __name__ == "__main__":
    main()
