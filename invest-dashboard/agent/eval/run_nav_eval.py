#!/usr/bin/env python3
"""Golden behavior for the NAV measure and a balance-sheet SOTP core."""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import measure_contract, measures, prisms, profile  # noqa: E402


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AGENT_PROFILE_DB"] = str(Path(tmp) / "profiles.db")
        bur = profile.build("BUR")
        check("BUR router proposes NAV", bur.measure == profile.NAV)
        check("NAV is an implemented measure", bur.measure_implemented)
        specialty = profile.build(
            "SPECIALTY", research_type="specialty_finance", has_stakes=False)
        check("specialty-finance router proposes NAV",
              specialty.measure == profile.NAV)

        assumptions = {
            "book_value": 3_000_000_000,
            "shares": 218_000_000,
            "dps": 0,
            "scenarios": {"base": {
                "roe": 0.13,
                "coe": 0.10,
                "g": 0.03,
                "peer_discount": 0.23,
            }},
            "stakes": [{
                "name": "YPF claim",
                "ownership_pct": 1.0,
                "entity_valuation": {"base": 1_000_000_000},
            }],
        }
        result = measures.calculate("BUR", assumptions)
        check("zero DPS does not break NAV", not result.is_refusal())
        check("BUR corridor matches reviewed NAV plus YPF option",
              result.corridor == (18.26, 21.54))

        holding = dataclasses.replace(
            bur, measure=profile.SOTP,
            measure_reason="NAV core plus separately valued claims",
            core=bur,
        )
        with patch("agent.measures.profile.build", return_value=holding):
            composed = measures.calculate("BUR", {
                "core": {key: value for key, value in assumptions.items()
                         if key not in ("stakes", "dps")},
                "stakes": assumptions["stakes"],
            })
        check("SOTP composes with a NAV core", composed.corridor == result.corridor)

        fit = {name: (pros, cons) for name, pros, cons in prisms.measure_fit(
            prisms.signals({"research_type": "litigation_finance"},
                           {"fcf": [-100, 20, -50], "has_stakes": True}))}
        check("measure-fit contains a non-empty NAV row",
              bool(fit["nav"][0]) and bool(fit["nav"][1]))
        check("measure contract knows NAV",
              measure_contract.REQUIRED_NODES[profile.NAV] ==
              ("head_comps", "material_for_measure"))

    print("NAV golden eval passed")


if __name__ == "__main__":
    main()
