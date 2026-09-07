"""Layer 3: an investment idea as a bet on one selected fork.

An idea deliberately keeps the ``Fork`` object, not its calculated corridor.
The corridor and returns are recalculated from the current basis whenever they
are requested.  The rebuild flag is an overlay and never changes lifecycle
status.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from . import fork as fork_mod

WATCHING = "watching"
ACTIVE = "active"
PASSED = "passed"
REJECTED = "rejected"
STATUSES = (WATCHING, ACTIVE, PASSED, REJECTED)
DRIFT_THRESHOLD = 0.30


@dataclass
class Idea:
    ticker: str
    fork: fork_mod.Fork
    entry: str
    horizon_months: int
    exit: str
    catalysts: tuple
    status: str
    baseline_mid: float
    rebuild_required: bool = False
    rebuild_reasons: tuple = ()


def build(basis, active_fork, *, entry, horizon_months, exit, catalysts,
          status=WATCHING):
    """Create an idea for an active fork; without one, create nothing."""
    if active_fork is None:
        return None
    if isinstance(catalysts, str):
        raise ValueError("idea catalysts must be a collection, not a string")
    catalysts = tuple(catalysts)
    _validate(entry, horizon_months, exit, catalysts, status)
    result = fork_mod.evaluate(basis, active_fork)
    if result.is_refusal() or not result.bettable:
        reason = "; ".join(result.refusals) or "fork is an observation, not a bet"
        raise ValueError(f"idea requires an active bettable fork: {reason}")
    return Idea(
        ticker=basis.ticker, fork=active_fork, entry=entry.strip(),
        horizon_months=horizon_months, exit=exit.strip(),
        catalysts=catalysts, status=status,
        baseline_mid=_mid(basis.corridor))


def expected_return(idea, current_basis, current_price=None):
    """Return total and annualized returns to the selected fork's live levels."""
    if current_basis.ticker != idea.ticker:
        raise ValueError("idea and baseline belong to different tickers")
    result = fork_mod.evaluate(current_basis, idea.fork)
    if result.is_refusal():
        raise ValueError("selected fork cannot be evaluated: "
                         + "; ".join(result.refusals))
    price = current_basis.price if current_price is None else current_price
    if price is None or price <= 0:
        raise ValueError("current price must be positive")
    return fork_mod.expected_return(price, result.corridor,
                                    idea.horizon_months)


def mark_drift(idea, current_basis, threshold=DRIFT_THRESHOLD):
    """Mark an idea when the baseline midpoint moved materially."""
    if current_basis.ticker != idea.ticker:
        raise ValueError("idea and baseline belong to different tickers")
    if threshold < 0:
        raise ValueError("drift threshold cannot be negative")
    old = idea.baseline_mid
    drift = abs(_mid(current_basis.corridor) - old) / abs(old) if old else float("inf")
    if drift >= threshold:
        _mark(idea, f"baseline drift {drift:.1%}")
    return idea


def mark_recheck(idea, outcome):
    """Overlay the rebuild mark when a selected fork condition failed."""
    if outcome.ticker != idea.ticker or outcome.fork_label != idea.fork.label:
        return idea
    if outcome.requires_decision:
        _mark(idea, "fork condition did not trigger")
    return idea


def rebuild(idea, current_basis, active_fork, **changes):
    """Reassemble on a fork, clearing marks while preserving lifecycle status."""
    if active_fork is None:
        raise ValueError("rebuild requires an active fork")
    allowed = {"entry", "horizon_months", "exit", "catalysts"}
    unknown = set(changes) - allowed
    if unknown:
        raise TypeError(f"unknown rebuild fields: {', '.join(sorted(unknown))}")
    values = {
        "entry": changes.get("entry", idea.entry),
        "horizon_months": changes.get("horizon_months", idea.horizon_months),
        "exit": changes.get("exit", idea.exit),
        "catalysts": changes.get("catalysts", idea.catalysts),
    }
    fresh = build(current_basis, active_fork, status=idea.status, **values)
    return replace(fresh, rebuild_required=False, rebuild_reasons=())


def _mark(idea, reason):
    idea.rebuild_required = True
    if reason not in idea.rebuild_reasons:
        idea.rebuild_reasons += (reason,)


def _validate(entry, horizon_months, exit, catalysts, status):
    if not isinstance(entry, str) or not entry.strip():
        raise ValueError("idea entry must be non-empty")
    if not isinstance(exit, str) or not exit.strip():
        raise ValueError("idea exit must be non-empty")
    if isinstance(horizon_months, bool) or not isinstance(horizon_months, int) \
            or horizon_months <= 0:
        raise ValueError("idea horizon must be a positive number of months")
    if status not in STATUSES:
        raise ValueError(f"unknown idea status: {status}")
    if any(not isinstance(item, str) or not item.strip() for item in catalysts):
        raise ValueError("each idea catalyst must be non-empty")


def _mid(corridor):
    return (corridor[0] + corridor[1]) / 2
