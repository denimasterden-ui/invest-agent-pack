#!/usr/bin/env python3
"""Regression eval for measure-contract gates at baseline/fork boundaries."""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import baseline, cli, coherence, profile  # noqa: E402


BANK_FACTS = coherence.Facts.from_dict({
    "ticker": "KSPI", "price": 105.55, "price_currency": "USD",
    "financial_currency": "KZT", "fx": 0.0021820245310664177,
    "shares": 190_027_266, "shares_source": "report",
    "net_income": 1_073_180_000_000, "eps": 5647.51,
    "book_value": 2_827_400_000_000, "period_end": "2026-06-30",
    "available_end": "2026-06-30", "dps_declared": 3994.8,
    "dps_declared_source": "report",
    "price_context": {"earnings_data_stale": False,
                      "most_recent_earnings_date": "2026-06-30"},
})


def assumptions_args(path, answer=None):
    return argparse.Namespace(
        ticker="DELL", assumptions=str(path), facts=None, rate=None,
        growth=None, rate_why=None, growth_why=None, answer=answer,
        price=100.0, quote_currency="USD",
    )


@patch("agent.baseline.prisms.gate", return_value=None)
def main(_prisms_gate):
    bank = profile.build("KSPI")
    with patch("agent.baseline.profile.build", return_value=bank), \
            patch("agent.baseline.store_client.materials_for_contract",
                  return_value=[{"for_measure": "ddm_ri"}]), \
            patch("agent.baseline.store_client.record_baseline") as record:
        empty = dataclasses.replace(bank, comps=())
        with patch("agent.baseline.profile.build", return_value=empty):
            refused = baseline.build("KSPI", BANK_FACTS, rate=.17, growth=.09,
                                     rate_why="rate", growth_why="growth")
        assert refused.is_refusal() and refused.corridor is None
        assert "head_comps" in refused.mismatches[0].message
        record.assert_not_called()

        calculated = baseline.build("KSPI", BANK_FACTS, rate=.17, growth=.09,
                                    rate_why="rate", growth_why="growth",
                                    persist=False)
        assert not calculated.is_refusal() and calculated.corridor

    with tempfile.TemporaryDirectory() as tmp:
        assumptions = Path(tmp) / "assumptions.json"
        assumptions.write_text(json.dumps({
            "fcf_base": 140, "shares": 10,
            "scenarios": {"base": {"growth_path": [.1] * 5,
                                      "discount_rate": .1,
                                      "terminal_growth": .02}},
        }), encoding="utf-8")
        answer = Path(tmp) / "forks.json"
        answer.write_text('{"forks": []}', encoding="utf-8")
        dell = profile.build("DELL")

        for command, args in (
                (cli.cmd_baseline, assumptions_args(assumptions)),
                (cli.cmd_fork, assumptions_args(assumptions, str(answer)))):
            output = io.StringIO()
            with patch("agent.cli.profile.build", return_value=dell), \
                    patch("agent.cli.store_client.materials_for_contract",
                          return_value=[{"for_measure": "sotp"}]), \
                    patch("agent.cli.baseline.build") as build, \
                    contextlib.redirect_stdout(output):
                assert command(args) == 3
            assert "material_for_measure" in output.getvalue()
            build.assert_not_called()

    sotp = dataclasses.replace(
        profile.build("NBIS"), measure="sotp",
        scope={"measure": "sotp", "confirmed_by": "human"},
        segments=("core", "cloud"), comps=("core",),
    )
    with patch("agent.baseline.profile.build", return_value=sotp), \
            patch("agent.baseline.store_client.materials_for_contract",
                  return_value=[{"for_measure": "sotp"}]), \
            patch("agent.baseline.measures.calculate") as calculate:
        refused = baseline.build("NBIS", assumptions={}, persist=False)
    assert refused.is_refusal()
    assert "part_comps:cloud" in refused.mismatches[0].message
    calculate.assert_not_called()

    # Head-comps of a holding-wrapped SOTP live on the core sub-profile
    # (Profile.effective), not the wrapper's own comps — the wrapper's comps
    # hold per-segment (part) comps instead. Emptying the wrapper alone would
    # leave NBIS's real core comps (IREN, CRWV) satisfying head_comps.
    empty_sotp = dataclasses.replace(
        sotp, core=dataclasses.replace(sotp.core, comps=()))
    with patch("agent.baseline.profile.build", return_value=empty_sotp), \
            patch("agent.baseline.store_client.materials_for_contract",
                  return_value=[{"for_measure": "sotp"}]), \
            patch("agent.baseline.measures.calculate") as calculate:
        refused = baseline.build("NBIS", assumptions={}, persist=False)
    assert refused.is_refusal()
    assert "head_comps" in refused.mismatches[0].message
    calculate.assert_not_called()

    covered_sotp = dataclasses.replace(sotp, comps=("core", "cloud"))
    with patch("agent.baseline.profile.build", return_value=covered_sotp), \
            patch("agent.baseline.store_client.materials_for_contract",
                  return_value=[{"for_measure": "sotp"}]), \
            patch("agent.baseline.measures.calculate") as calculate:
        calculate.return_value = type("Calculated", (), {
            "is_refusal": lambda self: False, "ticker": "NBIS",
            "measure": "sotp", "corridor": (1, 2)})()
        result = baseline.build("NBIS", assumptions={}, persist=False)
    assert not result.is_refusal()

    one_segment = dataclasses.replace(
        profile.build("DELL"), segments=("hardware",), comps=("HPE",))
    with patch("agent.baseline.profile.build", return_value=one_segment), \
            patch("agent.baseline.store_client.materials_for_contract",
                  return_value=[{"for_measure": "levered"}]), \
            patch("agent.baseline.measures.calculate") as calculate:
        calculate.return_value = type("Calculated", (), {
            "is_refusal": lambda self: False, "ticker": "DELL",
            "measure": "levered", "corridor": (1, 2)})()
        result = baseline.build("DELL", assumptions={}, persist=False)
    assert not result.is_refusal()
    print("measure contract wiring eval: passed")


if __name__ == "__main__":
    main()
