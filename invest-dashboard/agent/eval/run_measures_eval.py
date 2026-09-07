#!/usr/bin/env python3
"""Offline contract eval for profile-owned measures and physical caps."""
import os
import sys
from unittest.mock import patch
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import baseline, physical_caps, profile  # noqa: E402


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


@patch("agent.baseline.prisms.gate", return_value=None)
def main(_prisms_gate):
    with tempfile.TemporaryDirectory() as td:
        os.environ["AGENT_PROFILE_DB"] = str(Path(td) / "profiles.db")

        # FCF sign is deliberately the opposite of the old router's choice.
        dell = baseline.build("DELL", assumptions={
            "fcf_base": -100, "shares": 10,
            "scenarios": {"base": {"growth_path": [0.1] * 5,
                           "discount_rate": 0.10, "terminal_growth": 0.02}},
        })
        check("DELL is calculated with the profile's levered measure",
              dell.measure == profile.LEVERED and dell.corridor)

        chtr = baseline.build("DELL", assumptions={
            "fcf_base": 100, "shares": 10, "net_debt": 900,
            "scenarios": {"base": {"growth_path": [0.05] * 5,
                           "discount_rate": 0.10, "terminal_growth": 0.02}},
        })
        check("high net_debt/FCF (CHTR-like) flags refi risk on levered measure",
              any(w.code == "levered_debt_tail" for w in chtr.warnings)
              and "9.0" in next(w.message for w in chtr.warnings
                                 if w.code == "levered_debt_tail"))

        low_leverage = baseline.build("DELL", assumptions={
            "fcf_base": 100, "shares": 10, "net_debt": 200,
            "scenarios": {"base": {"growth_path": [0.05] * 5,
                           "discount_rate": 0.10, "terminal_growth": 0.02}},
        })
        check("low net_debt/FCF on levered measure does not warn",
              not any(w.code == "levered_debt_tail" for w in low_leverage.warnings))

        mu = baseline.build("MU", assumptions={
            "fcf_base": 100, "shares": 10, "base_year": "FY2026 peak",
            "scenarios": {"base": {"growth_path": [0.05] * 5,
                           "discount_rate": 0.10, "terminal_growth": 0.02}},
        }, physical={"cycle_position": "peak"})
        check("MU peak year is not silently accepted as the base",
              any(w.code == "cycle_peak_base" for w in mu.warnings))

        capped = baseline.build("DELL", assumptions={
            "fcf_base": 100, "shares": 10,
            "scenarios": {"base": {"growth_path": [0.1] * 5,
                           "discount_rate": 0.10, "terminal_growth": 0.05}},
        })
        check("terminal growth above the GDP ceiling is refused",
              capped.is_refusal() and "макропотолка" in capped.errors[0])

        core = {"revenue_base": 3e9, "shares": 500e6, "net_cash": 1e9,
                "scenarios": {"base": {"growth_path": [1, .6, .35, .22, .15],
                               "discount_rate": .10,
                               "terminal_fcf_margin": .25,
                               "terminal_fcf_multiple": 20}}}
        nbis = baseline.build("NBIS", assumptions={
            "fcf_base": 1, "core": core,
            "stakes": [{"name": "ClickHouse", "ownership_pct": .28,
                         "entity_valuation": {"base": 20e9}}],
        }, physical={"revenue_per_mw": 3e6, "contracted_gw": 5,
                     "build_rate_gw": 1})
        check("NBIS is calculated as SOTP from its profile despite positive FCF",
              nbis.measure == profile.SOTP and nbis.corridor)
        warning = next((w for w in nbis.warnings if w.code == "capacity_total"), None)
        check("capacity breach is a numeric warning, not a refusal",
              warning and "6.1" in warning.message and "5.0" in warning.message
              and not nbis.is_refusal())

        incomplete = physical_caps.check(profile.build("NBIS"), core["scenarios"], {})
        check("missing driver data does not refuse the run", isinstance(incomplete, tuple))

    print("All measures evals passed")


if __name__ == "__main__":
    main()
