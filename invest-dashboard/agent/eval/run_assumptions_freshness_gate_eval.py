#!/usr/bin/env python3
"""Regression eval for freshness at assumptions CLI orchestration (SPC-015)."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import cli  # noqa: E402


ASSUMPTIONS = {
    "ebitda": 100.0,
    "net_debt": 20.0,
    "shares": 10.0,
    "multiple_low": 8.0,
    "multiple_high": 10.0,
}


def args(assumptions, facts):
    return argparse.Namespace(
        ticker="DELL", assumptions=str(assumptions), facts=facts,
        rate=None, growth=None, rate_why=None, growth_why=None,
    )


def fork_args(assumptions, facts):
    return argparse.Namespace(**{
        **vars(args(assumptions, facts)), "answer": "unused.json",
        "price": 100.0, "quote_currency": "USD",
    })


def ingest_args(assumptions, facts):
    return argparse.Namespace(**{
        **vars(args(assumptions, facts)), "material": "unused.txt",
        "source_name": None, "answer": "unused.json", "out": None,
    })


def calculated():
    return SimpleNamespace(
        is_refusal=lambda: False, corridor=(1.0, 2.0), ticker="DELL",
        measure="levered", measure_reason="test", warnings=(),
    )


def main():
    with patch("agent.cli.prisms.gate", return_value=None), \
            patch("agent.baseline.prisms.gate", return_value=None), \
            tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        assumptions = tmp / "assumptions.json"
        assumptions.write_text(json.dumps(ASSUMPTIONS), encoding="utf-8")
        stale = tmp / "stale.json"
        stale.write_text(json.dumps({"price_context": {
            "earnings_data_stale": True,
            "most_recent_earnings_date": "2026-09-03",
        }}), encoding="utf-8")
        fresh = tmp / "fresh.json"
        fresh.write_text(json.dumps({"price_context": {
            "earnings_data_stale": False,
            "most_recent_earnings_date": "2026-06-30",
        }}), encoding="utf-8")

        with patch("agent.cli.store_client.get_material", return_value=[]), \
                patch("agent.cli.baseline.build") as build:
            assert cli.cmd_baseline(args(assumptions, str(stale))) == 3
            build.assert_not_called()

        material = [{"created_at": "2026-09-03T12:00:00Z"}]
        with patch("agent.cli.store_client.get_material", return_value=material), \
                patch("agent.cli.baseline.build") as build:
            build.return_value = calculated()
            cli.cmd_baseline(args(assumptions, str(stale)))
            assert build.call_args.kwargs["assumptions"] == ASSUMPTIONS
            assert "facts" not in build.call_args.kwargs

        with patch("agent.cli.baseline.build") as build:
            assert cli.cmd_baseline(args(assumptions, None)) == 3
            build.assert_not_called()

        with patch("agent.cli.store_client.get_material") as get_material, \
                patch("agent.cli.baseline.build") as build:
            build.return_value = calculated()
            cli.cmd_baseline(args(assumptions, str(fresh)))
            get_material.assert_not_called()
            build.assert_called_once()

        with patch("agent.cli.store_client.get_material", return_value=[]), \
                patch("agent.cli._baseline_for_fork") as build:
            assert cli.cmd_fork(fork_args(assumptions, str(stale))) == 3
            build.assert_not_called()

    print("assumptions freshness CLI eval: 5 passed")


if __name__ == "__main__":
    main()
