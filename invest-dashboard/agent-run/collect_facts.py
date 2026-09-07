#!/usr/bin/env python3
"""Prepare an editable facts JSON from Yahoo Finance statements.

Quarterly statements are preferred.  Annual statements are used only when a
complete quarterly pair is unavailable.  ``shares`` and ``dps_declared`` are
deliberately left for a human to copy from the issuer's report.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path


def _statement(ticker, name):
    """Read one yfinance property, treating provider failures as no data."""
    try:
        value = getattr(ticker, name)
    except Exception:
        return None
    return value


def _has_data(frame):
    return hasattr(frame, "columns") and len(frame.columns) > 0


def _common_period(income, balance):
    income_dates = {_date(column) for column in income.columns}
    balance_dates = {_date(column) for column in balance.columns}
    common = income_dates & balance_dates
    return max(common) if common else None


def _date(value):
    """Normalize pandas Timestamp, datetime, date, or an ISO date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    converted = getattr(value, "to_pydatetime", lambda: None)()
    if converted is not None:
        return converted.date()
    return date.fromisoformat(str(value)[:10])


def select_statements(ticker):
    """Return the freshest coherent statement pair and its common period.

    yfinance normally orders columns newest-first, but that is not part of the
    selection contract: the date is computed, not inferred from ``columns[0]``.
    Both statements must contain the selected period so income and book value
    cannot silently come from different reporting dates.
    """
    quarterly_income = _statement(ticker, "quarterly_income_stmt")
    quarterly_balance = _statement(ticker, "quarterly_balance_sheet")
    if _has_data(quarterly_income) and _has_data(quarterly_balance):
        period = _common_period(quarterly_income, quarterly_balance)
        if period is not None:
            return quarterly_income, quarterly_balance, period, "quarterly"

    annual_income = _statement(ticker, "income_stmt")
    annual_balance = _statement(ticker, "balance_sheet")
    if _has_data(annual_income) and _has_data(annual_balance):
        period = _common_period(annual_income, annual_balance)
        if period is not None:
            return annual_income, annual_balance, period, "annual"

    raise RuntimeError("yfinance did not return a common income/balance period")


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _row(frame, names):
    for name in names:
        if name in frame.index:
            return frame.loc[name]
    return None


def _period_value(frame, names, period):
    row = _row(frame, names)
    if row is None:
        return None
    for column in frame.columns:
        if _date(column) == period:
            return _number(row[column])
    return None


def _trailing_value(frame, names, period, frequency):
    """Return annual value, or TTM from up to four reported quarters."""
    row = _row(frame, names)
    if row is None:
        return None
    if frequency == "annual":
        return _period_value(frame, names, period)
    columns = sorted((column for column in frame.columns
                      if _date(column) <= period),
                     key=_date, reverse=True)[:4]
    values = [_number(row[column]) for column in columns]
    if not values or any(value is None for value in values):
        return None
    return sum(values)


def _net_debt(balance, period):
    """Return provider net debt, or debt less cash for the selected period."""
    reported = _period_value(balance, ("Net Debt",), period)
    if reported is not None:
        return reported
    debt = _period_value(balance, ("Total Debt",), period)
    cash = _period_value(
        balance,
        ("Cash Cash Equivalents And Short Term Investments",
         "Cash And Cash Equivalents", "Cash Financial"),
        period)
    if debt is None or cash is None:
        return None
    return debt - cash


def _text(value):
    """Return provider text without leaking pandas NaN into JSON."""
    if value is None:
        return None
    text = str(value).strip()
    return None if not text or text.lower() in ("nan", "nat", "none", "<na>") else text


def _rows(frame):
    """Normalize a provider DataFrame into dated plain dictionaries."""
    if frame is None or not hasattr(frame, "iterrows"):
        return []
    rows = []
    for index, values in frame.iterrows():
        try:
            row_date = _date(index)
        except (TypeError, ValueError):
            continue
        getter = getattr(values, "get", lambda _name, default=None: default)
        rows.append((row_date, getter))
    return sorted(rows, key=lambda item: item[0])


def _market_rows(history):
    rows = []
    for row_date, get in _rows(history):
        close = _number(get("Close"))
        if close is None:
            continue
        rows.append({
            "date": row_date,
            "close": close,
            "high": _number(get("High")) or close,
            "low": _number(get("Low")) or close,
            "volume": _number(get("Volume")),
        })
    return rows


def _mean(values):
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _ratio(value, base):
    if value is None or base in (None, 0):
        return None
    return round(value / base, 2)


def _move(current, previous):
    if current is None or previous in (None, 0):
        return None
    return round((current / previous - 1) * 100, 2)


def _range(rows, as_of, days):
    selected = [
        row for row in rows if row["date"] >= as_of - timedelta(days=days)
    ]
    if not selected:
        return {"low": None, "high": None, "position": None}
    low = min(row["low"] for row in selected)
    high = max(row["high"] for row in selected)
    close = rows[-1]["close"]
    position = None if high == low else round((close - low) / (high - low), 4)
    return {"low": low, "high": high, "position": position}


def build_price_context(history, earnings_dates=None, period_end=None,
                        upcoming_days=14):
    """Build group H from six months of daily prices.

    Percent moves are percentage points.  Relative volume compares a session
    with up to 20 preceding sessions, so a capitulation day does not inflate
    its own denominator.
    """
    rows = _market_rows(history)
    if not rows:
        return {}
    as_of = rows[-1]["date"]
    previous = rows[-2] if len(rows) > 1 else None
    prior_volume = _mean([row["volume"] for row in rows[-21:-1]])
    last = rows[-1]
    last_session = {
        "date": as_of.isoformat(),
        "move_pct": _move(last["close"], previous["close"] if previous else None),
        "volume": last["volume"],
        "average_volume_20d": (
            round(prior_volume, 2) if prior_volume is not None else None),
        "volume_vs_average": _ratio(last["volume"], prior_volume),
    }

    all_earnings = _rows(earnings_dates)
    past_earnings = [item for item in all_earnings if item[0] <= as_of]
    future_earnings = [item for item in all_earnings if item[0] > as_of]
    most_recent_earnings = past_earnings[-1][0] if past_earnings else None
    next_earnings = future_earnings[0][0] if future_earnings else None
    earnings_upcoming = (
        next_earnings is not None
        and next_earnings <= as_of + timedelta(days=upcoming_days)
    )
    normalized_period_end = _date(period_end) if period_end is not None else None
    earnings_data_stale = (
        most_recent_earnings is not None
        and normalized_period_end is not None
        and most_recent_earnings > normalized_period_end
    )

    reaction = None
    if past_earnings:
        earnings_date = most_recent_earnings
        session_index = next((i for i, row in enumerate(rows)
                              if row["date"] >= earnings_date), None)
        if session_index is not None:
            session = rows[session_index]
            before = rows[max(0, session_index - 20):session_index]
            previous_close = (
                rows[session_index - 1]["close"] if session_index else None)
            average_volume = _mean([row["volume"] for row in before])
            reaction = {
                "earnings_date": earnings_date.isoformat(),
                "session_date": session["date"].isoformat(),
                "move_pct": _move(session["close"], previous_close),
                "volume_vs_average": _ratio(session["volume"], average_volume),
            }

    return {
        "as_of": as_of.isoformat(),
        "range_1m": _range(rows, as_of, 30),
        "range_3m": _range(rows, as_of, 90),
        "recent_sessions": [
            {"date": row["date"].isoformat(), "close": row["close"],
             "volume": row["volume"]}
            for row in rows[-5:]
        ],
        "last_session": last_session,
        "six_month": {
            "ath": max(row["high"] for row in rows),
            "atl": min(row["low"] for row in rows),
        },
        "next_earnings_date": (
            next_earnings.isoformat() if next_earnings else None),
        "earnings_upcoming": earnings_upcoming,
        "earnings_warning": (
            "катализатор впереди, вход отложить"
            if earnings_upcoming else None),
        "most_recent_earnings_date": (
            most_recent_earnings.isoformat()
            if most_recent_earnings else None),
        "earnings_data_stale": earnings_data_stale,
        "earnings_staleness_warning": (
            "есть свежий отчёт, которого нет в данных"
            if earnings_data_stale else None),
        "last_earnings_reaction": reaction,
    }


def build_capital_signals(insider_transactions, as_of=None):
    """Build group G from yfinance's disclosed insider transactions."""
    normalized = []
    for index, values in getattr(insider_transactions, "iterrows", lambda: [])():
        get = getattr(values, "get", lambda _name, default=None: default)
        raw_date = get("Start Date", index)
        try:
            transaction_date = _date(raw_date)
        except (TypeError, ValueError):
            continue
        normalized.append({
            "date": transaction_date.isoformat(),
            "insider": _text(get("Insider")),
            "position": _text(get("Position")),
            "transaction": _text(get("Text")),
            "shares": _number(get("Shares")),
            "value": _number(get("Value")),
            "ownership": _text(get("Ownership")),
        })
    normalized.sort(key=lambda item: item["date"], reverse=True)
    if normalized:
        boundary = (_date(as_of) if as_of
                    else date.fromisoformat(normalized[0]["date"]))
        cutoff = boundary - timedelta(days=183)
        normalized = [item for item in normalized
                      if cutoff <= date.fromisoformat(item["date"]) <= boundary]
    return {"insider_transactions": normalized}


def _provider_value(ticker, name, *args, **kwargs):
    try:
        value = getattr(ticker, name)
        return value(*args, **kwargs) if callable(value) else value
    except Exception:
        return None


def collect(symbol):
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    income, balance, period, frequency = select_statements(ticker)
    info = ticker.info or {}
    fast_info = ticker.fast_info
    price = _number(getattr(fast_info, "last_price", None))
    price_currency = (getattr(fast_info, "currency", None)
                      or info.get("currency"))
    financial_currency = info.get("financialCurrency") or price_currency
    history = _provider_value(ticker, "history", period="6mo", auto_adjust=False)
    earnings_dates = _provider_value(ticker, "get_earnings_dates", limit=8)
    price_context = build_price_context(history, earnings_dates, period)
    capital_signals = build_capital_signals(
        _provider_value(ticker, "insider_transactions"),
        price_context.get("as_of") or period)

    return {
        "ticker": symbol,
        "price": price,
        "price_currency": price_currency,
        "financial_currency": financial_currency,
        "shares": None,
        "shares_source": "",
        "net_income": _trailing_value(
            income, ("Net Income", "Net Income Common Stockholders"),
            period, frequency),
        "eps": _trailing_value(
            income, ("Diluted EPS", "Basic EPS"), period, frequency),
        "book_value": _period_value(
            balance, ("Stockholders Equity", "Common Stock Equity",
                      "Total Equity Gross Minority Interest"), period),
        "net_debt": _net_debt(balance, period),
        "period_end": period.isoformat(),
        "available_end": period.isoformat(),
        "fx": None,
        "dps_declared": None,
        "dps_declared_source": "",
        "dps_trailing": _number(info.get("dividendRate")),
        "capital_signals": capital_signals,
        "price_context": price_context,
        "source": f"yfinance {frequency} statements",
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker", help="yfinance symbol, for example DELL")
    parser.add_argument("--out", type=Path,
                        help="write JSON here instead of stdout")
    args = parser.parse_args(argv)
    rendered = json.dumps(collect(args.ticker), ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
