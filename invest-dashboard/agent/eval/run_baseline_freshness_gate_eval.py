#!/usr/bin/env python3
"""Regression eval for the bank facts freshness boundary (SPC-015)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import baseline, coherence  # noqa: E402


RAW = {
    "ticker": "KSPI",
    "price": 105.55,
    "price_currency": "USD",
    "financial_currency": "KZT",
    "fx": 0.0021820245310664177,
    "shares": 190_027_266,
    "shares_source": "отчёт за 2К26",
    "net_income": 1_073_180_000_000,
    "eps": 5647.51,
    "book_value": 2_827_400_000_000,
    "period_end": "2026-06-30",
    "available_end": "2026-06-30",
    "dps_declared": 3994.8,
    "dps_declared_source": "отчёт за 2К26",
}


def build(price_context, materials):
    facts = coherence.Facts.from_dict({**RAW, "price_context": price_context})
    with patch("agent.baseline.store_client.get_material", return_value=materials) as get:
        result = baseline.build(
            "KSPI", facts, rate=0.17, growth=0.09,
            rate_why="ставка KZT", growth_why="рост KZT", persist=False,
        )
    return result, get


@patch("agent.baseline.prisms.gate", return_value=None)
def main(_prisms_gate):
    stale = {
        "earnings_data_stale": True,
        "most_recent_earnings_date": "2026-09-03",
    }

    refused, get = build(stale, [])
    assert refused.is_refusal() and refused.corridor is None
    mismatch = next(m for m in refused.mismatches if m.code == "freshness")
    assert "2026-09-03" in mismatch.message
    assert "ingest" in mismatch.request
    get.assert_called_once_with("KSPI")

    calculated, get = build(stale, [{"created_at": "2026-09-03T00:00:00Z"}])
    assert not calculated.is_refusal() and calculated.corridor is not None
    get.assert_called_once_with("KSPI")

    refused, get = build({}, [])
    assert refused.is_refusal() and refused.corridor is None
    mismatch = next(m for m in refused.mismatches if m.code == "freshness")
    assert "price_context" in mismatch.message
    assert "collect_facts" in mismatch.request
    get.assert_not_called()

    fresh, get = build({"earnings_data_stale": False,
                        "most_recent_earnings_date": "2026-06-30"}, [])
    assert not fresh.is_refusal()
    get.assert_not_called()

    print("baseline freshness gate eval: 4 passed")


if __name__ == "__main__":
    main()
