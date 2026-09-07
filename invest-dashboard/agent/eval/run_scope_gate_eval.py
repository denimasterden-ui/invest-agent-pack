#!/usr/bin/env python3
"""Regression eval for the valuation-scope gate at calculation boundaries."""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import baseline, cli, coherence, profile, scope  # noqa: E402


SEGMENTS = ({"name": "devices"}, {"name": "services"})
DECISION = {
    "measure": "levered",
    "reason": "единый денежный поток отражает обе части",
    "confirmed_by": "analyst",
}


def _profile(ticker, *, segments=SEGMENTS, decision=None):
    original = profile.build(ticker)
    return dataclasses.replace(
        original, segments=segments, scope=decision or {})


def _facts():
    return coherence.Facts.from_dict({
        "ticker": "KSPI", "price": 105.55, "price_currency": "USD",
        "financial_currency": "KZT", "fx": 0.0021820245310664177,
        "shares": 190_027_266, "shares_source": "отчёт",
        "net_income": 1_073_180_000_000, "eps": 5647.51,
        "book_value": 2_827_400_000_000, "period_end": "2026-06-30",
        "available_end": "2026-06-30", "dps_declared": 3994.8,
        "dps_declared_source": "отчёт", "price_context": {
            "earnings_data_stale": False,
            "most_recent_earnings_date": "2026-06-30",
        },
    })


def _args(assumptions, facts):
    return argparse.Namespace(
        ticker="DELL", assumptions=str(assumptions), facts=str(facts),
        rate=None, growth=None, rate_why=None, growth_why=None,
    )


def main():
    message = scope.gate({"segments": SEGMENTS})
    assert all(word in message for word in
               ("devices", "services", "levered", "blended", "sotp",
                "обоснован", "set scope", "confirm"))
    assert scope.gate({"segments": SEGMENTS, "scope": DECISION}) is None
    assert scope.gate({"segments": []}) is None
    assert scope.gate({"segments": SEGMENTS[:1]}) is None

    blocked_profile = _profile("KSPI")
    with patch("agent.baseline.profile.build", return_value=blocked_profile), \
            patch("agent.baseline.measures.calculate") as calculate:
        refused = baseline.build("KSPI", _facts(), rate=.17, growth=.09,
                                 rate_why="ставка", growth_why="рост",
                                 persist=False)
        assert refused.is_refusal() and refused.corridor is None
        mismatch = next(m for m in refused.mismatches if m.code == "scope")
        assert "devices" in mismatch.message and "set scope" in mismatch.request
        calculate.assert_not_called()

    allowed_profile = _profile("KSPI", decision=DECISION)
    with patch("agent.baseline.profile.build", return_value=allowed_profile):
        calculated = baseline.build(
            "KSPI", _facts(), rate=.17, growth=.09,
            rate_why="ставка", growth_why="рост", persist=False)
        assert not calculated.is_refusal() and calculated.corridor is not None

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        assumptions = tmp / "assumptions.json"
        assumptions.write_text(json.dumps({
            "ebitda": 100, "net_debt": 20, "shares": 10,
            "multiple_low": 8, "multiple_high": 10,
        }), encoding="utf-8")
        facts = tmp / "facts.json"
        facts.write_text(json.dumps({"price_context": {
            "earnings_data_stale": False,
            "most_recent_earnings_date": "2026-06-30",
        }}), encoding="utf-8")
        args = _args(assumptions, facts)
        fork_args = argparse.Namespace(**{
            **vars(args), "answer": "unused.json", "price": 100,
            "quote_currency": "USD",
        })
        blocked_profile = _profile("DELL")
        for command, command_args, boundary in (
                (cli.cmd_baseline, args, "agent.cli.baseline.build"),
                (cli.cmd_fork, fork_args, "agent.cli._baseline_for_fork")):
            output = io.StringIO()
            with patch("agent.cli.profile.build", return_value=blocked_profile), \
                    patch(boundary) as downstream, \
                    contextlib.redirect_stdout(output):
                assert command(command_args) == 3
            downstream.assert_not_called()
            rendered = output.getvalue()
            assert "scope" in rendered and "devices" in rendered

        allowed_profile = _profile("DELL", decision=DECISION)
        calculated = SimpleNamespace(
            is_refusal=lambda: False, corridor=(78.0, 98.0), ticker="DELL",
            measure="levered", measure_reason="test", warnings=(),
        )
        with patch("agent.cli.profile.build", return_value=allowed_profile), \
                patch("agent.cli.baseline.build", return_value=calculated) as build, \
                contextlib.redirect_stdout(io.StringIO()):
            assert cli.cmd_baseline(args) == 0
        build.assert_called_once()

    print("scope calculation gate eval: 10 passed")


if __name__ == "__main__":
    main()
