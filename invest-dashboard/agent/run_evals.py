#!/usr/bin/env python3
"""Run every agent behavior eval, including golden freshness outcomes."""
import argparse
import contextlib
import dataclasses
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = Path(__file__).with_name("eval")
FRESHNESS_FIXTURE = EVAL_DIR / "fixtures" / "freshness_cases.json"
SCOPE_COVERAGE_FIXTURE = EVAL_DIR / "fixtures" / "scope_coverage_cases.json"
MEASURE_CONTRACT_FIXTURE = EVAL_DIR / "fixtures" / "measure_contract_cases.json"
PRISM_FORM_FIXTURE = EVAL_DIR / "fixtures" / "prism_form_cases.json"
sys.path.insert(0, str(ROOT))


def _freshness_args(case, assumptions_path, facts_path):
    return argparse.Namespace(
        ticker=case["ticker"], assumptions=assumptions_path, facts=facts_path,
        rate=case.get("rate"), growth=case.get("growth"),
        rate_why=case.get("rate_why"), growth_why=case.get("growth_why"),
    )


def run_freshness_cases():
    """Exercise both public calculation paths with a deterministic material store."""
    from agent import cli, profile

    payload = json.loads(FRESHNESS_FIXTURE.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise AssertionError("freshness fixture: unsupported schema_version")
    with tempfile.TemporaryDirectory() as tmp_dir:
        old_profile_db = os.environ.get("AGENT_PROFILE_DB")
        os.environ["AGENT_PROFILE_DB"] = str(Path(tmp_dir) / "profiles.db")
        try:
            for index, case in enumerate(payload["cases"]):
                actual_measure = profile.build(case["ticker"]).measure
                if actual_measure != case["measure"]:
                    raise AssertionError(
                        f'{case["id"]}: profile measure {actual_measure!r}, '
                        f'expected {case["measure"]!r}')
                case_dir = Path(tmp_dir) / str(index)
                case_dir.mkdir()
                facts_path = case_dir / "facts.json"
                facts = (case["facts"] if "facts" in case else
                         {"price_context": case["price_context"]})
                facts_path.write_text(json.dumps(facts), encoding="utf-8")
                assumptions_path = None
                if "assumptions" in case:
                    assumptions = case_dir / "assumptions.json"
                    assumptions.write_text(
                        json.dumps(case["assumptions"]), encoding="utf-8")
                    assumptions_path = str(assumptions)
                args = _freshness_args(case, assumptions_path, str(facts_path))
                stdout = io.StringIO()
                with patch("agent.cli.store_client.get_material",
                           return_value=case["materials"]), \
                        patch("agent.baseline.store_client.get_material",
                              return_value=case["materials"]), \
                        patch("agent.cli.measure_contract.gate",
                              return_value=None), \
                        patch("agent.baseline.measure_contract.gate",
                              return_value=None), \
                        patch("agent.cli.prisms.gate", return_value=None), \
                        patch("agent.baseline.prisms.gate", return_value=None), \
                        contextlib.redirect_stdout(stdout):
                    status = cli.cmd_baseline(args)
                got = ("refusal" if status == 3 else
                       "calculated" if status == 0 else status)
                if got != case["expected"]:
                    raise AssertionError(
                        f'{case["id"]}: got {got!r}, '
                        f'expected {case["expected"]!r}')
                output = stdout.getvalue()
                if got == "refusal" and ("ОТКАЗ" not in output or
                                          "коридор" in output):
                    raise AssertionError(
                        f'{case["id"]}: refusal leaked a calculated corridor')
                if got == "calculated" and "коридор" not in output:
                    raise AssertionError(
                        f'{case["id"]}: calculation has no corridor')
                print(f'PASS freshness {case["id"]}: {got}')
        finally:
            if old_profile_db is None:
                os.environ.pop("AGENT_PROFILE_DB", None)
            else:
                os.environ["AGENT_PROFILE_DB"] = old_profile_db


def run_scope_coverage_cases():
    """Lock scope outcomes and multi-segment peer coverage without a network."""
    from agent import baseline, profile

    payload = json.loads(SCOPE_COVERAGE_FIXTURE.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise AssertionError("scope coverage fixture: unsupported schema_version")
    assumptions = {
        "fcf_base": 140, "shares": 10,
        "scenarios": {"base": {
            "growth_path": [.1] * 5, "discount_rate": .1,
            "terminal_growth": .02,
        }},
    }
    original = profile.build("DELL")
    for case in payload["scope_cases"]:
        prof = dataclasses.replace(
            original, segments=tuple(case["segments"]), scope=case["scope"])
        with patch("agent.baseline.profile.build", return_value=prof), \
                patch("agent.cli.prisms.gate", return_value=None), \
                patch("agent.baseline.prisms.gate", return_value=None):
            result = baseline.build("DELL", assumptions=assumptions,
                                    persist=False)
        got = "refusal" if result.is_refusal() else "calculated"
        if got != case["expected"]:
            raise AssertionError(
                f'{case["id"]}: got {got!r}, expected {case["expected"]!r}')
        print(f'PASS scope {case["id"]}: {got}')

    import importlib.util
    collector_path = ROOT / "agent-run" / "collect_peers.py"
    spec = importlib.util.spec_from_file_location("golden_collect_peers",
                                                  collector_path)
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    for case in payload["coverage_cases"]:
        searches = case["search_results"]

        class FakeSearch:
            def __init__(self, query, max_results):
                self.quotes = [
                    {"symbol": symbol, "quoteType": "EQUITY"}
                    for symbol in searches.get(query, [])
                ]

        class FakeTicker:
            def __init__(self, symbol):
                self.info = {"forwardPE": 10.0}

        fake_yf = type("FakeYF", (), {
            "Search": FakeSearch,
            "Ticker": FakeTicker,
        })
        prof = dataclasses.replace(original, segments=tuple(case["segments"]))
        with patch.object(collector.profile, "build", return_value=prof):
            result = collector.collect(prof.ticker, yf_module=fake_yf)
        got = result["comps_cover_segments"]
        if got is not case["expected_comps_cover_segments"]:
            raise AssertionError(
                f'{case["id"]}: comps_cover_segments={got!r}, '
                f'expected {case["expected_comps_cover_segments"]!r}')
        if result.get("note") != case["expected_note"]:
            raise AssertionError(
                f'{case["id"]}: note={result.get("note")!r}, '
                f'expected {case["expected_note"]!r}')
        print(f'PASS coverage {case["id"]}: {got}')


def main():
    # Эвалы гоняются ЛОКАЛЬНО (стаб/фикстуры), не в канон: снимаем
    # INVEST_MCP_URL на весь прогон, иначе baseline.build/record эвал-кейсов
    # пишет фикстуры в боевой канон (subprocess-эвалы наследуют окружение).
    mcp = os.environ.pop("INVEST_MCP_URL", None)
    try:
        if not MEASURE_CONTRACT_FIXTURE.is_file():
            raise AssertionError("measure contract golden fixture is missing")
        if not PRISM_FORM_FIXTURE.is_file():
            raise AssertionError("prism form golden fixture is missing")
        run_freshness_cases()
        run_scope_coverage_cases()
        scripts = sorted(EVAL_DIR.glob("run_*_eval.py"))
        for script in scripts:
            print(f"==> {script.relative_to(ROOT)}", flush=True)
            completed = subprocess.run([sys.executable, str(script)], cwd=ROOT)
            if completed.returncode:
                return completed.returncode
        print(f"all {len(scripts)} agent evals passed")
        return 0
    finally:
        if mcp is not None:
            os.environ["INVEST_MCP_URL"] = mcp


if __name__ == "__main__":
    raise SystemExit(main())
