#!/usr/bin/env python3
"""Offline behavior eval for canonical profiles outside tickers.py (SPC-022)."""
import contextlib
import io
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import cli, profile  # noqa: E402
from agent.kernel import store_client, tickers  # noqa: E402


LIVE_FACTS = {
    "symbol": "SPCZZ",
    "quoteType": "EQUITY",
    "longName": "SPC Test Corporation",
    "currency": "USD",
    "regularMarketPrice": 42.0,
}


def main():
    assert "SPCZZ" not in tickers.TICKERS_BY_KEY
    profiles = {}
    creates = []

    def canon(name, arguments):
        ticker = arguments.get("ticker")
        if name == "get_profile":
            return profiles.get(ticker, [])
        if name == "create_profile":
            creates.append(arguments)
            profiles[ticker] = dict(arguments, version=1)
            return profiles[ticker]
        raise AssertionError(f"unexpected canonical call: {name}")

    with patch.dict(os.environ, {"INVEST_MCP_URL": "https://canon.invalid/mcp"}), \
            patch.object(store_client, "_call_tool", side_effect=canon), \
            patch.object(store_client, "_symbol_facts", return_value=LIVE_FACTS):
        entry = cli._resolve("spczz")
        first = profile.build(entry["key"])
        second_entry = store_client.resolve_ticker("SPCZZ")
        second = profile.build(second_entry["key"])

    assert first.ticker == "SPCZZ" and first.research_type == "default"
    assert second.ticker == "SPCZZ"
    assert entry["name"] == "SPC Test Corporation" and entry["currency"] == "USD"
    assert second_entry["key"] == "SPCZZ"
    assert len(creates) == 1, f"create_profile called {len(creates)} times"
    assert "SPCZZ" in profiles, "seeded ticker is absent from canonical roster"

    with patch.dict(os.environ, {"INVEST_MCP_URL": "https://canon.invalid/mcp"}), \
            patch.object(store_client, "_call_tool", return_value=[]), \
            patch.object(store_client, "_symbol_facts", return_value={}):
        try:
            store_client.resolve_ticker("TYPOZZ")
        except ValueError as error:
            message = str(error)
        else:
            raise AssertionError("dead symbol produced an empty profile")
    assert "TYPOZZ" in message and "факт" in message.lower(), message

    holding = {
        "ticker": "HOLDZZ", "measure": "sotp", "version": 1,
        "drivers": ["стоимость долей"], "data_gaps": [],
        "comps": [[{"name": "CoreCo", "kind": "public"}]],
        "updated_at": "2026-09-14",
    }
    output = io.StringIO()
    with patch.object(cli, "_resolve", return_value={"key": "HOLDZZ", "name": "Holding"}), \
            patch.object(store_client, "get_profile", return_value=holding), \
            patch.object(store_client, "get_scenarios", return_value={"baseline": None, "forks": []}), \
            patch.object(store_client, "list_materials", return_value=[]), \
            patch.object(store_client, "get_drift", return_value=[]), \
            contextlib.redirect_stdout(output):
        assert cli.cmd_show(SimpleNamespace(ticker="HOLDZZ", detail=False,
                                            explain=False)) == 0
    rendered = output.getvalue()
    assert "CoreCo" in rendered and "object has no attribute" not in rendered, rendered
    print("PASS: dynamic symbol seeds once, dead symbol refuses, holding comps render")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
