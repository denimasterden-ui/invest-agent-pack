#!/usr/bin/env python3
"""Регрессия issuer-anchor guard для смешанного первоисточника (SPC-012 A4)."""
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agent import ingest  # noqa: E402


def _parse(anchor_class, source):
    answer = json.dumps({
        "profile_updates": [{
            "field": "comps",
            "value": ["NKE"],
            "reason": "макро-компонента влияет на сектор",
            "anchor_class": anchor_class,
            "source": source,
        }],
        "baseline_checks": [],
        "thesis_candidates": [],
    })
    return ingest.parse(answer, "LULU", "разбор тезиса Burry")


def main():
    mixed = _parse(ingest.MACRO_GUIDANCE, "LULU Q1 + Michigan Sentiment")
    if any(problem.code == "anchor" for problem in mixed.problems):
        print("FAIL: смешанный источник ошибочно форсит company_guide")
        return 1

    issuer = _parse(ingest.MACRO_GUIDANCE, "LULU quarterly report")
    if not any(problem.code == "anchor" for problem in issuer.problems):
        print("FAIL: чистый источник эмитента больше не форсит company_guide")
        return 1

    print("PASS: смешанный источник не форсится, чистый эмитент форсится")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
