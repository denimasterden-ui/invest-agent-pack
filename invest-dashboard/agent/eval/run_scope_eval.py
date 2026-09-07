#!/usr/bin/env python3
"""Offline unit table for the pure scope gate core."""
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import scope  # noqa: E402


def check(description, condition):
    if not condition:
        raise AssertionError(description)
    print(f"PASS {description}")


def main():
    segment_cases = (
        ([], False),
        ([{"name": "core"}], False),
        ([{"name": "core"}, {"name": "cloud"}], True),
        (["core", "cloud", "ads"], True),
    )
    for segments, expected in segment_cases:
        result = scope.assess({"segments": segments})
        check(f"{len(segments)} segments -> multi_segment={expected}",
              result.multi_segment is expected and result.segments == segments)
        check("assessment has a human-readable reason",
              isinstance(result.reason, str) and bool(result.reason.strip()))

    decision_cases = (
        ({"measure": "sotp", "confirmed_by": "analyst"}, True),
        ({"measure": "sotp"}, False),
        ({"confirmed_by": "analyst"}, False),
        ({}, False),
    )
    for decision, expected in decision_cases:
        result = scope.assess({"scope": decision})
        check(f"scope {decision!r} -> has_decision={expected}",
              result.has_decision is expected)

    segments = [{"name": "core"}, {"name": "cloud"}]
    check("all segments covered by peer_kind",
          scope.comps_cover_segments(segments, [
              {"ticker": "A", "peer_kind": "core"},
              {"ticker": "B", "peer_kind": "cloud"},
          ]))
    check("all segments covered by comp name",
          scope.comps_cover_segments(["core", "cloud"], [
              {"name": "core"}, {"name": "cloud"},
          ]))
    check("missing segment is not covered",
          not scope.comps_cover_segments(segments, [
              {"ticker": "A", "peer_kind": "core"},
          ]))
    check("empty comps do not cover declared segments",
          not scope.comps_cover_segments(segments, []))
    check("empty segments need no coverage",
          scope.comps_cover_segments([], []))


if __name__ == "__main__":
    main()
