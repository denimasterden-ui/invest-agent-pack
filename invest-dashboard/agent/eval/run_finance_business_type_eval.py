#!/usr/bin/env python3
"""Regression contract for financial business-model tags (SPC-020)."""
import sys
import dataclasses
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import prisms, profile  # noqa: E402
from agent.kernel import tickers  # noqa: E402


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def fit_for(ticker, facts):
    entry = tickers.TICKERS_BY_KEY[ticker]
    signals = prisms.signals(entry, facts)
    return entry, {name: (pros, cons)
                   for name, pros, cons in prisms.measure_fit(signals)}


def main():
    expected_types = {
        "KSPI": "balance_finance",
        "2318.HK": "balance_finance",
        "FISV": "asset_light_finance",
        "PYPL": "asset_light_finance",
        "TIGR": "asset_light_finance",
        "MRX": "asset_light_finance",
        "MELI": "marketplace",
    }
    for ticker, expected in expected_types.items():
        check(f"{ticker}: substantive business_type",
              tickers.TICKERS_BY_KEY[ticker]["business_type"] == expected)

    bank_entry, bank_fit = fit_for(
        "KSPI", {"fcf": [1, 1, 1], "price": 100, "shares": 100,
                 "net_debt": 200})
    check("KSPI: ddm_ri gets balance-sheet rationale",
          any("ROE/book/payout" in pro for pro in bank_fit["ddm_ri"][0]))
    check("KSPI: profile router remains a bank",
          profile._kind("KSPI", bank_entry["research_type"], False,
                        bank_entry["business_type"]) == "bank")

    processor_entry, processor_fit = fit_for(
        "FISV", {"fcf": [3.8, 4.0, 4.3], "price": 160,
                 "shares": 560_000_000, "net_debt": 25_000_000_000})
    check("FISV: levered gets positive FCF rationale",
          "FCF>0" in processor_fit["levered"][0] and
          "FCF стабилен" in processor_fit["levered"][0])
    check("FISV: asset-light tag supports levered/ev_revenue",
          any("asset-light" in pro
              for measure in ("levered", "ev_revenue")
              for pro in processor_fit[measure][0]))
    check("FISV: no false financial-business FCF objection",
          not any("FCF не та мера" in con
                  for con in processor_fit["levered"][1]))
    check("FISV: profile router remains an operating business",
          profile._kind("FISV", processor_entry["research_type"], False,
                        processor_entry["business_type"])
          == "generic")
    routed_signals = prisms.signals(
        dataclasses.asdict(profile.build("FISV")), {"fcf": [3.8, 4.0, 4.3]})
    check("FISV: built profile carries registry classification into prisms",
          routed_signals["business_type"] == "asset_light_finance")

    print("All finance business-type evals passed")


if __name__ == "__main__":
    main()
