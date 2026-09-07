#!/usr/bin/env python3
"""Offline behavior eval for the idea layer (SPC-008)."""
import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import fork, idea, recheck  # noqa: E402


def basis(corridor=(100.0, 120.0), price=100.0):
    return fork.Basis("TEST", "ddm_ri", "test", price, "USD", corridor,
                      dps=10.0)


def active_fork():
    return fork.Fork(
        label="bull", thesis="dividend grows", channel=fork.DIVIDEND,
        channel_reason="dividend measures the thesis", horizon_months=18,
        must_be_true=("dividend is declared",), overrides={
            fork.DPS_FORWARD: fork.Override(
                150.0, "declared", fork.COMPANY_GUIDE, "report", fork.NUMBER),
            fork.REQUIRED_YIELD: fork.Override(
                1.0, "exit yield", fork.ANALYST_JUDGEMENT, "", fork.ESTIMATE),
        })


def main():
    base, selected = basis(), active_fork()
    made = idea.build(base, selected, entry="now", horizon_months=24,
                      exit="at fork corridor", catalysts=("annual report",),
                      status=idea.ACTIVE)
    assert made is not None
    assert made.fork is selected
    assert not hasattr(made, "corridor") and not hasattr(made, "fork_corridor")

    returns = idea.expected_return(made, base, current_price=100.0)
    assert returns["low"] == (0.5, round(1.5 ** 0.5 - 1, 6))
    assert returns["mid"] == returns["high"] == returns["low"]
    assert returns["years"] == 2.0

    drifted = basis((140.0, 168.0))
    assert idea.mark_drift(made, drifted) is made
    assert made.rebuild_required and made.status == idea.ACTIVE

    rebuilt = idea.rebuild(made, drifted, selected)
    assert not rebuilt.rebuild_required and rebuilt.status == idea.ACTIVE

    failed = recheck.RecheckOutcome(
        ticker="TEST", fork_label="bull", fork_thesis="dividend grows",
        conditions=(recheck.ConditionCheck("dividend is declared",
                                           recheck.NOT_TRIGGERED, "not declared"),),
        requires_decision=True, deferred=None)
    idea.mark_recheck(rebuilt, failed)
    assert rebuilt.rebuild_required and rebuilt.status == idea.ACTIVE

    rebuilt = idea.rebuild(rebuilt, drifted, selected)
    assert not rebuilt.rebuild_required and rebuilt.status == idea.ACTIVE
    assert idea.build(base, None, entry="now", horizon_months=12,
                      exit="target", catalysts=()) is None

    fields = {f.name for f in dataclasses.fields(made)}
    assert "fork" in fields and "corridor" not in fields
    print("idea eval: PASS")


if __name__ == "__main__":
    main()
