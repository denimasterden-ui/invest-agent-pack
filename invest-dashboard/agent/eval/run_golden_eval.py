#!/usr/bin/env python3
"""Offline golden behavior eval for the complete SPC-008 v2 pipeline.

Frozen market facts and model answers are compared with reviewed outcomes.
No provider, network client, or subscription-backed model is used.
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import baseline, coherence, fork, peers, profile, recheck  # noqa: E402

FIXTURE = Path(__file__).with_name("fixtures") / "golden_cases.json"


class GoldenMismatch(AssertionError):
    pass


def expect(ticker, layer, got, wanted, subject):
    if got != wanted:
        raise GoldenMismatch(
            f"{ticker} [{layer}] {subject}: получено {got!r}, эталон {wanted!r}")


def run_case(case):
    ticker = case["ticker"]
    expected_profile = case["profile"]
    prof = profile.build(ticker)
    expect(ticker, "profile", prof.measure, expected_profile["measure"], "measure")
    expect(ticker, "profile", prof.business_kind,
           expected_profile["business_kind"], "business_kind")
    if "effective_kind" in expected_profile:
        expect(ticker, "profile", prof.effective.business_kind,
               expected_profile["effective_kind"], "effective.business_kind")

    base_data = case["baseline"]
    if "facts" in base_data:
        facts = coherence.Facts.from_dict(base_data["facts"])
        base = baseline.build(
            ticker, facts, rate=base_data["rate"], growth=base_data["growth"],
            rate_why=base_data["rate_why"], growth_why=base_data["growth_why"])
    else:
        base = baseline.build(ticker, assumptions=base_data["assumptions"],
                              physical=base_data.get("physical"))
    if base.is_refusal():
        detail = getattr(base, "errors", None) or getattr(base, "mismatches", None)
        raise GoldenMismatch(f"{ticker} [baseline] отказ: {detail!r}")
    expect(ticker, "baseline", list(base.corridor),
           base_data["expected_corridor"], "corridor")
    warning_codes = [warning.code for warning in getattr(base, "warnings", ())]
    expect(ticker, "baseline", warning_codes,
           base_data["expected_warning_codes"], "warning codes")

    peer_band = None
    if case.get("peers_fixture"):
        peer_data = json.loads((FIXTURE.parent / case["peers_fixture"]).read_text(
            encoding="utf-8"))
        peer_band = peers.build(prof, peer_data)
        got_band = list(peer_band.band) if peer_band and peer_band.band else None
        expect(ticker, "peers", got_band, case["expected_peer_band"], "band")

    parsed, problems = fork.parse(json.dumps(case["fork_answer"], ensure_ascii=False))
    if problems or len(parsed) != 1:
        raise GoldenMismatch(f"{ticker} [fork/parse] {problems!r}, forks={len(parsed)}")
    basis = fork.basis(base, price=case["price"],
                       price_currency=case["price_currency"])
    branch = fork.evaluate(basis, parsed[0], peer_band=peer_band)
    if branch.is_refusal():
        raise GoldenMismatch(f"{ticker} [fork/evaluate] отказ: {branch.refusals!r}")
    expected_fork = case["expected_fork"]
    expect(ticker, "fork", branch.fork.label, expected_fork["label"], "label")
    expect(ticker, "fork", list(branch.corridor), expected_fork["corridor"],
           "corridor")
    expect(ticker, "fork", len(branch.warnings),
           expected_fork["warning_count"], "warning count")
    if expected_fork.get("warning_contains"):
        if not any(expected_fork["warning_contains"] in item
                   for item in branch.warnings):
            raise GoldenMismatch(
                f"{ticker} [fork] предупреждение не содержит "
                f"{expected_fork['warning_contains']!r}: {branch.warnings!r}")

    checks = tuple(recheck.ConditionCheck(condition, frozen["status"], frozen["fact"])
                   for condition, frozen in zip(parsed[0].must_be_true,
                                                case["recheck"]))
    outcome = recheck.check(parsed[0], basis, checks)
    if outcome.is_refusal():
        raise GoldenMismatch(f"{ticker} [recheck] отказ: {outcome.refusals!r}")
    expected_recheck = case["expected_recheck"]
    expect(ticker, "recheck", [item.status for item in outcome.conditions],
           expected_recheck["statuses"], "statuses")
    expect(ticker, "recheck", outcome.requires_decision,
           expected_recheck["requires_decision"], "requires_decision")
    deferred = (outcome.deferred.horizon_months - parsed[0].horizon_months
                if outcome.deferred else 0)
    expect(ticker, "recheck", deferred,
           expected_recheck["deferred_months"], "deferred months")

    return {
        "ticker": ticker, "profile": prof.measure,
        "baseline": list(base.corridor), "baseline_warnings": warning_codes,
        "convergence": getattr(base, "divergence", None),
        "fork": list(branch.corridor), "fork_warnings": list(branch.warnings),
        "recheck": [item.status for item in outcome.conditions],
    }


def evaluate_all(payload):
    threshold = payload["convergence_threshold"]
    results = [run_case(case) for case in payload["cases"]]
    for result in results:
        divergence = result["convergence"]
        if divergence is not None and divergence > threshold:
            raise GoldenMismatch(
                f"{result['ticker']} [convergence] расхождение {divergence:.2%} "
                f"> порога {threshold:.2%}")
    return results


def main():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expect("ALL", "fixture", {case["ticker"] for case in payload["cases"]},
           {"KSPI", "NBIS", "DELL", "MU"}, "four golden tickers")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AGENT_PROFILE_DB"] = str(Path(tmp) / "profiles.db")
        with patch("agent.baseline.prisms.gate", return_value=None):
            first = evaluate_all(payload)
            second = evaluate_all(payload)
    expect("ALL", "repeatability", second, first, "second run")

    print("golden eval: frozen profile → baseline → fork → recheck")
    for result in first:
        divergence = result["convergence"]
        convergence = (f"{divergence:.2%} (limit {payload['convergence_threshold']:.2%})"
                       if divergence is not None else "n/a (one measure)")
        print(f"PASS {result['ticker']}: convergence={convergence}; "
              f"base={result['baseline']}; fork={result['fork']}; "
              f"recheck={result['recheck']}")
    print("PASS repeatability: identical result on the second offline run")


if __name__ == "__main__":
    main()
