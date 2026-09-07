#!/usr/bin/env python3
"""Регрессия SPC-009 §2.2: сборщик предпочитает свежий квартал году."""
import importlib.util
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "agent-run" / "collect_facts.py"


def load_module():
    spec = importlib.util.spec_from_file_location("collect_facts", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def frame(*dates):
    class Frame:
        columns = [date.fromisoformat(value) for value in dates]
    return Frame()


class EmptyFrame:
    columns = []


class Ticker:
    quarterly_income_stmt = frame("2026-04-30", "2026-07-31")
    quarterly_balance_sheet = frame("2026-07-31", "2026-04-30")
    income_stmt = frame("2026-01-31")
    balance_sheet = frame("2026-01-31")


class AnnualOnlyTicker:
    quarterly_income_stmt = EmptyFrame()
    quarterly_balance_sheet = EmptyFrame()
    income_stmt = frame("2025-01-31", "2026-01-31")
    balance_sheet = frame("2026-01-31", "2025-01-31")


class Table:
    def __init__(self, rows):
        self._rows = rows

    def iterrows(self):
        return iter(self._rows)


def main():
    module = load_module()

    income, balance, period_end, frequency = module.select_statements(Ticker())
    assert income is Ticker.quarterly_income_stmt
    assert balance is Ticker.quarterly_balance_sheet
    assert period_end == date(2026, 7, 31)
    assert frequency == "quarterly"

    income, balance, period_end, frequency = module.select_statements(
        AnnualOnlyTicker())
    assert income is AnnualOnlyTicker.income_stmt
    assert balance is AnnualOnlyTicker.balance_sheet
    assert period_end == date(2026, 1, 31)
    assert frequency == "annual"

    history = Table([
        (date(2026, 8, 31), {"Close": 100, "Volume": 100}),
    ])
    earnings = Table([
        (datetime(2026, 8, 15), {"Reported EPS": 1.2}),
        (datetime(2026, 9, 10), {"Reported EPS": None}),
        (datetime(2026, 12, 10), {"Reported EPS": None}),
    ])
    context = module.build_price_context(
        history, earnings, period_end=date(2026, 7, 31))
    assert context["next_earnings_date"] == "2026-09-10"
    assert context["earnings_upcoming"] is True
    assert "вход отложить" in context["earnings_warning"]
    assert context["most_recent_earnings_date"] == "2026-08-15"
    assert context["earnings_data_stale"] is True
    assert "нет в данных" in context["earnings_staleness_warning"]

    print("collect facts eval: 14 passed")


if __name__ == "__main__":
    main()
