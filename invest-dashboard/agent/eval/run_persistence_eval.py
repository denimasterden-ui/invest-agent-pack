#!/usr/bin/env python3
"""SPC-009 §2.5: результаты baseline/fork сохраняются, drift читается."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import baseline, fork, profile_store  # noqa: E402


def _assumptions(value):
    return {
        "fcf_base": 100.0,
        "shares": 10.0,
        "scenarios": {"base": {
            "growth_path": [value] * 5,
            "discount_rate": 0.10,
            "terminal_growth": 0.02,
        }},
    }


@patch("agent.baseline.prisms.gate", return_value=None)
def main(_prisms_gate):
    failures = []
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "agent.db"
        os.environ["AGENT_PROFILE_DB"] = str(path)
        try:
            first = baseline.build("DELL", assumptions=_assumptions(0.05))
            second = baseline.build("DELL", assumptions=_assumptions(0.10))

            parsed, problems = fork.parse(json.dumps({"forks": [{
                "label": "bull", "thesis": "денежный поток растёт",
                "channel": "поток", "channel_reason": "меняется поток",
                "horizon_months": 12, "must_be_true": ["FCF растёт"],
                "overrides": {"growth_path": {
                    "value": [0.15] * 5, "rationale": "ускорение",
                    "anchor_class": "company_guide", "confirms": "driver",
                    "source": "гайд компании"
                }}
            }]}))
            if problems or not parsed:
                failures.append(f"валидный форк должен разбираться: {problems}")
            else:
                made = fork.evaluate(fork.basis(second, price=20,
                                                  price_currency="USD"), parsed[0])
                if made.is_refusal():
                    failures.append(f"валидный форк должен считаться: {made.refusals}")

            con = sqlite3.connect(path)
            con.row_factory = sqlite3.Row
            try:
                tables = {r[0] for r in con.execute(
                    "select name from sqlite_master where type='table'")}
                if not {"profile_events", "baselines", "forks"} <= tables:
                    failures.append(f"в agent.db нет новых таблиц: {tables}")
                bases = con.execute(
                    "select ticker, run_at, measure, corridor_low, corridor_high, "
                    "assumptions_json, status from baselines order by id").fetchall()
                saved_forks = con.execute(
                    "select ticker, run_at, measure, label, corridor_low, "
                    "corridor_high, assumptions_json, status from forks order by id"
                ).fetchall()
            finally:
                con.close()
            if len(bases) != 2 or any(r["ticker"] != "DELL" for r in bases):
                failures.append("каждый baseline-прогон должен добавлять строку")
            if bases and (not bases[0]["run_at"] or bases[0]["measure"] != "levered"
                          or json.loads(bases[0]["assumptions_json"]) != _assumptions(0.05)
                          or bases[0]["status"] != "calculated"):
                failures.append("baseline должен хранить дату, меру, допущения и статус")
            if len(saved_forks) != 1 or saved_forks[0]["label"] != "bull" \
                    or saved_forks[0]["status"] != "calculated":
                failures.append("fork должен хранить результат и статус")

            cli = subprocess.run(
                [sys.executable, str(ROOT / "agent" / "cli.py"), "drift", "DELL"],
                cwd=ROOT, text=True, capture_output=True, env=os.environ.copy())
            if cli.returncode or "DELL" not in cli.stdout or "→" not in cli.stdout \
                    or "%" not in cli.stdout:
                failures.append(f"drift должен показывать движение коридора: {cli.stdout!r}")
        finally:
            os.environ.pop("AGENT_PROFILE_DB", None)

    for failure in failures:
        print("FAIL", failure)
    print("Все PASS" if not failures else f"{len(failures)} FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
