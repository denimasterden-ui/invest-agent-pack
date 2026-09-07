#!/usr/bin/env python3
"""Регрессия SPC-009 §2.3/§3.6: группы H и G профиля."""
import importlib.util
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
MODULE_PATH = ROOT / "agent-run" / "collect_facts.py"


def load_module():
    spec = importlib.util.spec_from_file_location("collect_facts", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Table:
    def __init__(self, rows):
        self._rows = rows

    def iterrows(self):
        return iter(self._rows)


def main():
    os.environ["AGENT_PROFILE_DB"] = tempfile.mktemp(suffix=".db")
    from agent import profile
    from agent.coherence import Facts

    module = load_module()
    history = Table([
        (date(2026, 6, 2), {"Open": 80, "Close": 80, "Volume": 100}),
        (date(2026, 7, 30), {"Open": 100, "Close": 100, "Volume": 100}),
        (date(2026, 7, 31), {"Open": 99, "Close": 90, "Volume": 400}),
        (date(2026, 8, 3), {"Open": 90, "Close": 95, "Volume": 200}),
        (date(2026, 8, 31), {"Open": 110, "Close": 110, "Volume": 100}),
    ])
    earnings = Table([
        (datetime(2026, 7, 31), {"Reported EPS": 1.2}),
    ])

    context = module.build_price_context(history, earnings)
    assert context["as_of"] == "2026-08-31"
    assert context["range_1m"] == {"low": 95.0, "high": 110.0,
                                    "position": 1.0}
    assert context["range_3m"]["low"] == 80.0
    assert context["six_month"] == {"ath": 110.0, "atl": 80.0}
    assert len(context["recent_sessions"]) == 5
    assert context["recent_sessions"][-1] == {
        "date": "2026-08-31", "close": 110.0, "volume": 100.0}
    assert context["last_session"]["move_pct"] == 15.79
    assert context["last_session"]["volume_vs_average"] == 0.5
    reaction = context["last_earnings_reaction"]
    assert reaction["earnings_date"] == "2026-07-31"
    assert reaction["session_date"] == "2026-07-31"
    assert reaction["move_pct"] == -10.0
    assert reaction["volume_vs_average"] == 4.0

    insiders = Table([
        (0, {"Start Date": datetime(2026, 8, 20), "Insider": "A. Owner",
             "Position": "CEO", "Text": "Sale", "Shares": 500,
             "Value": 50000, "Ownership": "D"}),
        (1, {"Start Date": datetime(2026, 7, 1), "Insider": "B. Owner",
             "Position": "Director", "Text": "Purchase", "Shares": 100,
             "Value": 9000, "Ownership": "I"}),
    ])
    signals = module.build_capital_signals(insiders)
    assert signals["insider_transactions"][0] == {
        "date": "2026-08-20", "insider": "A. Owner", "position": "CEO",
        "transaction": "Sale", "shares": 500.0, "value": 50000.0,
        "ownership": "D"}

    built = profile.build("DELL")
    assert built.price_context == {}
    assert built.capital_signals == {}
    assert built.core is None
    assert profile.record_change(
        "DELL", "price_context", context, "контекст на дату сбора")
    assert profile.record_change(
        "DELL", "capital_signals", signals, "раскрытия инсайдеров")
    rebuilt = profile.build("DELL")
    assert rebuilt.price_context == context
    assert rebuilt.capital_signals == signals

    raw = dict(ticker="DELL", price=110, price_currency="USD",
               financial_currency="USD", shares=10, shares_source="report",
               net_income=100, eps=10, book_value=200,
               period_end="2026-07-31", available_end="2026-07-31",
               price_context=context, capital_signals=signals)
    facts = Facts.from_dict(raw)
    assert facts.price_context == context
    assert facts.capital_signals == signals

    print("context signals eval: 22 passed")


if __name__ == "__main__":
    main()
