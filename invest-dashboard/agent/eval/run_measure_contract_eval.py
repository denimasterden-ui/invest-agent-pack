#!/usr/bin/env python3
"""Golden behavior and unit table for the measure research contract."""
import dataclasses
import argparse
import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import baseline, cli, measure_contract, profile as profile_module, scope  # noqa: E402


FIXTURE = Path(__file__).with_name("fixtures") / "measure_contract_cases.json"


def check(description, condition):
    if not condition:
        raise AssertionError(description)
    print(f"PASS {description}")


def profile(measure="levered", comps=None, segments=None):
    return {
        "measure": measure,
        "comps": [] if comps is None else comps,
        "segments": [] if segments is None else segments,
    }


def run_golden_cases():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise AssertionError("measure contract fixture: unsupported schema_version")
    original = profile_module.build("DELL")
    calculated = type("Calculated", (), {
        "is_refusal": lambda self: False,
        "ticker": "DELL", "measure": "levered", "corridor": (1, 2),
    })()
    for case in payload["cases"]:
        scope_value = ({"measure": case["measure"], "confirmed_by": "eval"}
                       if len(case["segments"]) >= 2 else {})
        prof = dataclasses.replace(
            original, measure=case["measure"],
            segments=tuple(case["segments"]), comps=tuple(case["comps"]),
            scope=scope_value, core=None)
        with patch("agent.baseline.profile.build", return_value=prof), \
                patch("agent.baseline.store_client.materials_for_contract",
                      return_value=case["materials"]), \
                patch("agent.baseline.prisms.gate", return_value=None), \
                patch("agent.baseline.measures.calculate",
                      return_value=calculated):
            result = baseline.build("DELL", assumptions={}, persist=False)
        got = "refusal" if result.is_refusal() else "calculated"
        check(f"golden {case['id']}: {got}", got == case["expected"])


def run_research_coverage_checks():
    collector_path = ROOT / "agent-run" / "collect_peers.py"
    spec = importlib.util.spec_from_file_location(
        "measure_contract_collect_peers", collector_path)
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    prof = dataclasses.replace(
        profile_module.build("DELL"), measure="sotp", core=None,
        segments=({"name": "core"}, {"name": "private assets"}),
        comps=({"name": "CoreCo", "segment": "core", "kind": "public"},))
    missing = collector.missing_contract_nodes(prof)
    check("research marks uncovered SOTP segment",
          missing == ["part_comps:private assets"])
    hint = collector.contract_coverage_hint(prof)
    check("research suggests private/pre-IPO comps", "private/pre-IPO" in hint)



def main():
    run_golden_cases()
    run_research_coverage_checks()
    expected_common = ("head_comps", "material_for_measure")
    for measure in ("ddm_ri", "levered", "ev_revenue"):
        check(f"{measure}: common nodes only",
              measure_contract.REQUIRED_NODES[measure] == expected_common)
    check("sotp: part comps declared",
          measure_contract.REQUIRED_NODES["sotp"] ==
          expected_common + ("part_comps",))

    sotp = profile("sotp", segments=["core", {"name": "cloud"}, "auto"])
    check("nodes_for expands one node per SOTP segment",
          measure_contract.nodes_for(sotp) == [
              "head_comps", "material_for_measure",
              "part_comps:core", "part_comps:cloud", "part_comps:auto",
          ])
    check("nodes_for does not expand parts for another measure",
          measure_contract.nodes_for(profile("levered", segments=["core"])) ==
          ["head_comps", "material_for_measure"])

    table = [
        ("empty head comps", profile(), [{"for_measure": "levered"}],
         ["head_comps"]),
        ("head comps present", profile(comps=["DELL"]),
         [{"for_measure": "levered"}], []),
        ("no material", profile(comps=["DELL"]), [],
         ["material_for_measure"]),
        ("material for another measure", profile(comps=["DELL"]),
         [{"for_measure": "sotp"}], ["material_for_measure"]),
        ("material for current measure", profile(comps=["DELL"]),
         [{"for_measure": "levered"}], []),
        ("sotp all segments covered", profile(
            "sotp",
            comps=[{"name": "A", "segment": "core", "kind": "public"},
                   {"name": "B", "segment": "cloud", "kind": "public"}],
            segments=["core", "cloud"]), [{"for_measure": "sotp"}], []),
        ("sotp missing one segment", profile(
            "sotp", comps=[{"name": "A", "segment": "core",
                            "kind": "public"}],
            segments=["core", "cloud"]), [{"for_measure": "sotp"}],
         ["part_comps:cloud"]),
        ("private comp covers segment", profile(
            "sotp", comps=[{"name": "VentureCo", "segment": "robotics",
                            "kind": "private", "valuation_ref": "Series D"}],
            segments=["robotics"]), [{"for_measure": "sotp"}], []),
        ("legacy string covers segment", profile(
            "sotp", comps=["core"], segments=["core"]),
         [{"for_measure": "sotp"}], []),
    ]
    for description, prof, materials, expected_missing in table:
        coverage = measure_contract.assess(prof, materials)
        check(description, coverage.missing == expected_missing and
              coverage.satisfied is (not expected_missing))

    unclear = measure_contract.assess(
        profile("sotp", comps=["core"], segments=["core", "cloud"]),
        [{"for_measure": "sotp"}])
    check("unclear segment comps point to a human",
          "человек" in unclear.reason.lower() and "cloud" in unclear.reason)
    check("gate lists missing nodes",
          "head_comps" in measure_contract.gate(profile(), []) and
          "material_for_measure" in measure_contract.gate(profile(), []))
    check("gate passes complete coverage",
          measure_contract.gate(profile(comps=["DELL"]),
                                [{"for_measure": "levered"}]) is None)

    segments = ["core", {"name": "robotics"}]
    check("scope accepts public/private comp dictionaries",
          scope.comps_cover_segments(segments, [
              {"name": "A", "segment": "core", "kind": "public"},
              {"name": "B", "segment": "robotics", "kind": "private",
               "valuation_ref": "last round"},
          ]))
    check("scope keeps legacy strings",
          scope.comps_cover_segments(["core", "cloud"], ["core", "cloud"]))
    check("head comps helper distinguishes empty/non-empty",
          not scope.has_head_comps([]) and
          not scope.has_head_comps(None) and
          scope.has_head_comps(["DELL"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
