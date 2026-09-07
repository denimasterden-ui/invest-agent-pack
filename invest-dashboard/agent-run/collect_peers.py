#!/usr/bin/env python3
"""Collect editable peer data and suggest peers when a profile has none."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import measure_contract, peers, profile, scope  # noqa: E402


FIELDS = (
    "forwardPE", "forwardEps", "priceToBook", "returnOnEquity",
    "marketCap", "totalDebt", "totalCash", "totalRevenue", "revenueGrowth",
    "currency", "financialCurrency",
)


def _fiscal_year_end(info):
    """Normalize Yahoo's fiscal-year timestamp/date to YYYY-MM-DD."""
    value = info.get("lastFiscalYearEnd") or info.get("fiscalYearEnd")
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.utcfromtimestamp(value).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10]).isoformat()
        except ValueError:
            return value or None
    return None


def peer_info(info):
    """Keep only the stable input schema consumed by agent.peers."""
    result = {name: info.get(name) for name in FIELDS if info.get(name) is not None}
    result["fiscal_year_end"] = _fiscal_year_end(info)
    return result


def suggest_candidates(business_kind, ticker, yf_module=None, limit=5):
    """Return equity symbols suggested by Yahoo for the profile's business kind."""
    if yf_module is None:
        import yfinance as yf_module
    try:
        quotes = yf_module.Search(business_kind, max_results=limit + 5).quotes
    except Exception:
        return ()
    candidates = []
    for quote in quotes or ():
        symbol = quote.get("symbol")
        quote_type = (quote.get("quoteType") or "").upper()
        if symbol and symbol != ticker and quote_type in ("EQUITY", ""):
            candidates.append(symbol)
        if len(candidates) == limit:
            break
    return tuple(candidates)


def _segment_value(segment, field, default=None):
    if isinstance(segment, dict):
        return segment.get(field, default)
    return default


def suggest_for_profile(prof, yf_module=None, limit=5):
    """Suggest tagged comps for every material segment of a profile."""
    segments = list(getattr(prof, "segments", ()) or ())
    suggestions = []
    tagged_comps = []
    for segment in segments:
        name = (_segment_value(segment, "name") or
                _segment_value(segment, "segment"))
        business_kind = (_segment_value(segment, "business_kind") or name)
        declared = _segment_value(segment, "comps", ()) or ()
        symbols = tuple(declared) if declared else suggest_candidates(
            business_kind, prof.ticker, yf_module=yf_module, limit=limit)
        suggestions.append({
            "segment": name,
            "business_kind": business_kind,
            "symbols": list(symbols),
        })
        tagged_comps.extend(
            {"ticker": symbol, "segment": name} for symbol in symbols)

    covered = scope.comps_cover_segments(segments, tagged_comps)
    result = {
        "comps_cover_segments": covered,
        "segments": suggestions,
    }
    if not covered:
        result["note"] = "comps покрывают не все сегменты"
    return result


def missing_contract_nodes(prof):
    """Return comp-related research nodes still empty for this profile."""
    # collect_peers cannot produce a report material, so assess that node as
    # already covered and report only the comp nodes this collector can help.
    coverage = measure_contract.assess(
        vars(prof), [{"for_measure": prof.measure}])
    return [node for node in coverage.missing
            if node == "head_comps" or node.startswith("part_comps:")]


def contract_coverage_hint(prof):
    """Human-facing soft warning; the hard refusal belongs to the gate."""
    missing = missing_contract_nodes(prof)
    if not missing:
        return ""
    hint = "незаполненные узлы контракта: " + ", ".join(missing)
    if any(node.startswith("part_comps:") for node in missing):
        hint += ("; если публичных аналогов нет, добавьте private/pre-IPO "
                 "comp с ориентиром оценки")
    return hint


def collect(ticker, yf_module=None):
    if yf_module is None:
        import yfinance as yf_module
    prof = profile.build(ticker)
    contract_missing = missing_contract_nodes(prof)
    effective = prof.effective
    segment_plan = (suggest_for_profile(prof, yf_module=yf_module)
                    if len(prof.segments) >= 2 else None)
    if segment_plan is not None:
        candidates = tuple(dict.fromkeys(
            symbol
            for segment in segment_plan["segments"]
            for symbol in segment["symbols"]
        ))
    else:
        candidates = effective.comps or suggest_candidates(
            effective.business_kind, ticker, yf_module=yf_module)
    symbols = (ticker,) + tuple(candidates)
    data = {}
    for symbol in symbols:
        try:
            data[symbol] = peer_info(yf_module.Ticker(symbol).info or {})
        except Exception as exc:
            data[symbol] = {"error": str(exc), "fiscal_year_end": None}
    if segment_plan is not None:
        data["_candidates"] = segment_plan
        data["comps_cover_segments"] = segment_plan["comps_cover_segments"]
        if not segment_plan["comps_cover_segments"]:
            data["note"] = segment_plan["note"]
    elif not effective.comps:
        data["_candidates"] = {
            "business_kind": effective.business_kind,
            "symbols": list(candidates),
            "note": "sector peers candidates; review before adding to profile comps",
        }
    data["_measure_contract"] = {
        "missing": contract_missing,
        "satisfied": not contract_missing,
        "hint": contract_coverage_hint(prof),
    }
    return data


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("ticker")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    data = collect(args.ticker)
    rendered = json.dumps(data, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    band = peers.build(profile.build(args.ticker), data)
    hint = data.get("_measure_contract", {}).get("hint")
    if hint:
        print(f"внимание: {hint}", file=sys.stderr)
    if data.get("comps_cover_segments") is False:
        print("внимание: comps покрывают не все сегменты", file=sys.stderr)
    for warning in getattr(band, "warnings", ()):
        print(f"внимание: {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
