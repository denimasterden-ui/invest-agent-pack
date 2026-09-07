#!/usr/bin/env python3
"""Unit table for the pure freshness gate core."""
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.freshness import Freshness, assess, gate, satisfied_by_material


def main():
    assess_cases = [
        (
            {"earnings_data_stale": True,
             "most_recent_earnings_date": "2026-09-03"},
            Freshness(True, True, "2026-09-03", "freshness context available"),
        ),
        (
            {"earnings_data_stale": False,
             "most_recent_earnings_date": "2026-06-01"},
            Freshness(True, False, "2026-06-01", "freshness context available"),
        ),
    ]
    for price_context, expected in assess_cases:
        assert assess(price_context) == expected

    empty = assess({})
    assert empty.has_context is False
    assert empty.stale is False
    assert empty.report_date is None
    assert "empty" in empty.reason

    missing = assess({"as_of": "2026-09-04", "price": 100})
    assert missing.has_context is False
    assert missing.stale is False
    assert missing.report_date is None
    assert "keys" in missing.reason

    material_cases = [
        ([{"created_at": "2026-09-04T01:02:03Z"}], True),
        ([{"created_at": "2026-09-03"}], True),
        ([{"created_at": "2026-09-02T23:59:59+00:00"}], False),
        ([], False),
        ([{"created_at": "not-a-date"}, {"created_at": ""}, {}], False),
    ]
    for materials, expected in material_cases:
        assert satisfied_by_material("2026-09-03", materials) is expected

    # Гейт устойчив к форме от get_material: MCP отдаёт ОДИН материал dict'ом,
    # а не списком из одного (регрессия SPC-015: escape-клапан не срабатывал
    # при ровно одном материале). Проверяем dict/list/пусто/None.
    stale = {"earnings_data_stale": True,
             "most_recent_earnings_date": "2026-09-03"}
    assert gate(stale, "X", lambda t: {"created_at": "2026-09-04"}) is None
    assert gate(stale, "X", lambda t: {"created_at": "2026-09-01"}) is not None
    assert gate(stale, "X", lambda t: [{"created_at": "2026-09-04"}]) is None
    assert gate(stale, "X", lambda t: []) is not None
    assert gate(stale, "X", lambda t: None) is not None
    fresh = {"earnings_data_stale": False,
             "most_recent_earnings_date": "2026-06-01"}
    assert gate(fresh, "X", lambda t: []) is None
    assert gate({}, "X", lambda t: []) is not None

    print("freshness eval: 16 passed")


if __name__ == "__main__":
    main()
