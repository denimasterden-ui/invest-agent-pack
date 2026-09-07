"""Pure research-coverage contract for each supported valuation measure."""
from dataclasses import dataclass

from agent import scope


REQUIRED_NODES: dict[str, tuple[str, ...]] = {
    "ddm_ri": ("head_comps", "material_for_measure"),
    "levered": ("head_comps", "material_for_measure"),
    "ev_revenue": ("head_comps", "material_for_measure"),
    "sotp": ("head_comps", "material_for_measure", "part_comps"),
}


@dataclass
class Coverage:
    satisfied: bool
    missing: list[str]
    reason: str


def nodes_for(profile: dict) -> list[str]:
    """Return concrete research nodes, expanding SOTP parts by segment."""
    nodes = []
    for node in REQUIRED_NODES[profile["measure"]]:
        if node == "part_comps":
            nodes.extend(
                f"part_comps:{_segment_name(segment)}"
                for segment in (profile.get("segments") or [])
            )
        else:
            nodes.append(node)
    return nodes


def assess(profile: dict, materials: list) -> Coverage:
    """Assess mechanically verifiable coverage for the profile's measure."""
    nodes = nodes_for(profile)
    comps = profile.get("comps")
    # A holding wrapper's own comps hold per-segment (part) comps; head-comps
    # of the consolidated entity live on the core sub-profile (same split
    # profile.py's _build_registry already uses via Profile.effective).
    core = profile.get("core")
    head_comps = core.comps if core is not None else comps
    measure = profile["measure"]
    covered = {
        "head_comps": scope.has_head_comps(head_comps),
        "material_for_measure": any(
            isinstance(material, dict) and material.get("for_measure") == measure
            for material in (materials or [])
        ),
    }

    segments = profile.get("segments") or []
    for segment in segments if measure == "sotp" else []:
        name = _segment_name(segment)
        covered[f"part_comps:{name}"] = scope.comps_cover_segments(
            [segment], comps or [])

    missing = [node for node in nodes if not covered[node]]
    if not missing:
        reason = "Все обязательные узлы ресёрча для выбранной меры покрыты."
    else:
        reason = "Не покрыты обязательные узлы: " + ", ".join(missing) + "."
        unclear_parts = [node.removeprefix("part_comps:") for node in missing
                         if node.startswith("part_comps:")]
        if "head_comps" in missing or unclear_parts:
            details = ", ".join(unclear_parts)
            target = f" по сегментам {details}" if details else " головной компании"
            reason += (
                f" Comps{target} не определены — нужен человек: назовите "
                "сопоставимых (в том числе private/venture) или подтвердите "
                "их отсутствие."
            )
    return Coverage(not missing, missing, reason)


def gate(profile: dict, materials: list) -> str | None:
    """Return a refusal when the selected measure's research is incomplete."""
    coverage = assess(profile, materials)
    if coverage.satisfied:
        return None
    return f"Отказ: {coverage.reason} Недостающие узлы: {', '.join(coverage.missing)}."


def _segment_name(segment) -> str:
    if isinstance(segment, dict):
        for field in ("name", "peer_kind", "segment"):
            if segment.get(field) is not None:
                return str(segment[field])
    return str(segment)
