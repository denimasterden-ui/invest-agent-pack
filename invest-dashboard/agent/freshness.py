"""Pure freshness checks for collected price context and materials."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable


_FRESHNESS_KEYS = {"earnings_data_stale", "most_recent_earnings_date"}


@dataclass(frozen=True)
class Freshness:
    has_context: bool
    stale: bool
    report_date: str | None
    reason: str


def assess(price_context: dict) -> Freshness:
    """Summarize the earnings freshness fields in ``price_context``."""
    if not price_context:
        return Freshness(False, False, None, "price_context is empty")
    if not _FRESHNESS_KEYS.intersection(price_context):
        return Freshness(
            False, False, None, "price_context has no freshness keys")
    return Freshness(
        True,
        bool(price_context.get("earnings_data_stale")),
        price_context.get("most_recent_earnings_date"),
        "freshness context available",
    )


def satisfied_by_material(report_date: str | None,
                          materials: list[dict]) -> bool:
    """Return whether a valid material was created on or after the report."""
    report_day = _iso_date(report_date)
    if report_day is None:
        return False
    return any(
        material_day is not None and material_day >= report_day
        for material_day in (
            _iso_date(material.get("created_at"))
            for material in materials
            if isinstance(material, dict)
        )
    )


def gate(price_context: dict, ticker: str,
         get_material: Callable[[str], list[dict]]) -> str | None:
    """Return a refusal reason when report freshness cannot be established."""
    status = assess(price_context)
    if not status.has_context:
        return "свежесть не проверить: нет price_context"
    if not status.stale:
        return None
    materials = get_material(ticker)
    # MCP отдаёт один материал dict'ом, а не списком из одного — сгладить,
    # иначе satisfied_by_material итерирует ключи и материал не находит
    # (escape-клапан гейта ломался для типового случая «ровно один материал»).
    if isinstance(materials, dict):
        materials = [materials]
    elif materials is None:
        materials = []
    if satisfied_by_material(status.report_date, materials):
        return None
    return f"вышел отчёт {status.report_date}, его разбор не подан"


def _iso_date(value: object) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()).date()
    except ValueError:
        return None
