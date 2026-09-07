#!/usr/bin/env python3
"""Offline behavior eval for prism signals from collected facts and CLI."""
import contextlib
import io
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).with_name("fixtures") / "prism_signal_cases.json"
sys.path.insert(0, str(ROOT))

from agent import cli, prisms  # noqa: E402


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    check("fixture schema", payload.get("schema_version") == 1)
    for case in payload["cases"]:
        actual = prisms.signals(case["profile"], case["facts"])
        check(f"real fact signals: {case['id']}", actual == case["expected"])

    case = payload["cases"][0]
    facts_path = FIXTURE.parent / "prism_cli_facts.tmp.json"
    try:
        facts_path.write_text(json.dumps(case["facts"]), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = cli.main(["prisms", case["ticker"],
                               "--facts", str(facts_path)])
        output = stdout.getvalue()
    finally:
        facts_path.unlink(missing_ok=True)

    check("cli exits successfully", status == 0)
    check("cli prints ticker and signal block",
          case["ticker"] in output and "Сток-признаки" in output
          and "fcf_sign" in output and "sbc_ratio" in output)
    check("cli prints catalog fields",
          "Каталог призм" in output and "признаки:" in output
          and "предлагает:" in output and "Marathon" in output)
    check("cli prints informed workflow hint",
          "прогони призмы против признаков → выбери меру → set scope "
          "<measure+reason> → confirm" in output)
    print("All prism CLI evals passed")


if __name__ == "__main__":
    main()
