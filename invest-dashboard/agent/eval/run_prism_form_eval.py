#!/usr/bin/env python3
"""Golden stock-form verdicts and mutation lock for the prism gate."""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "prism_form_cases.json"
sys.path.insert(0, str(ROOT))

from agent import baseline, prisms, profile  # noqa: E402


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS prism form {label}")


def _reason(case):
    prisms_used = ", ".join(case["applicable_prisms"])
    return f"{prisms_used}: {case['method']} → {case['measure']}"


def _run_form_cases(cases):
    catalog_names = {entry.name for entry in prisms.catalog()}
    for case in cases:
        if "expected_signals" not in case:
            continue
        actual = prisms.signals(case["profile"], case["facts"])
        check(f"{case['id']}: stock signals", actual == case["expected_signals"])
        check(f"{case['id']}: applicable prisms exist",
              set(case["applicable_prisms"]).issubset(catalog_names))
        decision = {
            "measure": case["measure"],
            "reason": _reason(case),
            "confirmed_by": "golden analyst",
        }
        assessed = prisms.justified({"scope": decision})
        check(f"{case['id']}: measure has prism rationale",
              assessed.measure_justified and
              prisms.gate({"scope": decision}, has_info=True) is None)


def _run_gate_lock(case):
    original = profile.build("DELL")
    prof = dataclasses.replace(
        original, measure=case["measure"], scope=case["scope"], core=None)
    calculated = SimpleNamespace(
        is_refusal=lambda: False, ticker="DELL", measure=case["measure"],
        measure_reason="should only be reached when the gate is disabled",
        corridor=(1, 2), warnings=(), errors=())
    with patch("agent.baseline.profile.build", return_value=prof), \
            patch("agent.baseline.measure_contract.gate", return_value=None), \
            patch("agent.baseline.measures.calculate",
                  return_value=calculated) as calculate:
        result = baseline.build(
            "DELL", facts=case["facts"], assumptions={}, persist=False)
    got = "refusal" if result.is_refusal() else "calculated"
    check(f"{case['id']}: {got}", got == case["expected"])
    check(f"{case['id']}: calculation stays behind gate",
          not calculate.called)


def main():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    check("fixture schema", payload.get("schema_version") == 1)
    cases = payload["cases"]
    _run_form_cases(cases)
    gate_cases = [case for case in cases if "expected" in case]
    check("exactly one gate control case", len(gate_cases) == 1)
    _run_gate_lock(gate_cases[0])
    print("All prism form evals passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
