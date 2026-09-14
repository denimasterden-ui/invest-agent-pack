#!/usr/bin/env python3
"""SPC-023: canon-only findings client and readable lesson CLI."""
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import cli  # noqa: E402
from agent.kernel import store_client  # noqa: E402


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main():
    with patch.dict(os.environ, {}, clear=True), \
            patch.object(store_client, "_call_tool") as call:
        check("local list is empty", store_client.list_findings() == [])
        check("local add is a no-op", store_client.add_finding(
            "run_lesson", "Не смешивать валюты", "Нормализовать валюту", "DELL"
        ) is None)
        check("local mode never calls canon", not call.called)

    calls = []

    def fake_call(name, arguments):
        calls.append((name, arguments))
        if name == "add_finding":
            return 73
        return [{
            "id": 73, "category": "run_lesson",
            "title": "Не смешивать валюты",
            "body": "Нормализовать валюту до сравнения.",
            "ticker": "DELL", "status": "open",
            "created_at": "2026-09-14T10:00:00+00:00",
        }]

    with patch.dict(os.environ, {"INVEST_MCP_URL": "https://canon.test/mcp"},
                    clear=True), patch.object(store_client, "_call_tool",
                                              side_effect=fake_call):
        finding_id = store_client.add_finding(
            "run_lesson", "Не смешивать валюты", "Нормализовать валюту", "DELL"
        )
        findings = store_client.list_findings("open")
    check("canon add returns id", finding_id == 73)
    check("canon list returns rows", findings and findings[0]["ticker"] == "DELL")
    check("canon calls use registered tool contracts", calls == [
        ("add_finding", {
            "category": "run_lesson", "title": "Не смешивать валюты",
            "body": "Нормализовать валюту", "ticker": "DELL",
        }),
        ("list_findings", {"status": "open"}),
    ])

    with tempfile.TemporaryDirectory() as td:
        body_path = Path(td) / "lesson.txt"
        body_path.write_text("Нормализовать валюту до сравнения.", encoding="utf-8")
        output = io.StringIO()
        with patch.dict(os.environ,
                        {"INVEST_MCP_URL": "https://canon.test/mcp"}, clear=True), \
                patch.object(store_client, "_call_tool", side_effect=fake_call), \
                contextlib.redirect_stdout(output):
            add_status = cli.main([
                "add-finding", "DELL", "--category", "run_lesson",
                "--title", "Не смешивать валюты", "--body", str(body_path),
            ])
            list_status = cli.main(["findings", "DELL"])
        rendered = output.getvalue()
    check("CLI commands exit successfully", add_status == list_status == 0)
    check("CLI prints lesson as prose", all(piece in rendered for piece in (
        "DELL", "Не смешивать валюты", "Нормализовать валюту до сравнения.",
        "run_lesson", "open",
    )))
    check("CLI does not dump raw JSON", "{'id':" not in rendered)

    print("All findings evals passed")


if __name__ == "__main__":
    main()
