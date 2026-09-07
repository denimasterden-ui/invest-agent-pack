"""Pure assessment helpers for valuation scope and peer coverage."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    multi_segment: bool
    segments: list
    has_decision: bool
    reason: str


def assess(profile) -> Scope:
    """Describe whether a profile needs, and has, a confirmed scope choice."""
    get = profile.get if isinstance(profile, dict) else \
        lambda field, default=None: getattr(profile, field, default)
    segments = get("segments") or []
    multi_segment = len(segments) >= 2
    selected_scope = get("scope") or {}
    has_decision = (bool(selected_scope.get("measure"))
                    and bool(selected_scope.get("confirmed_by")))

    if multi_segment and has_decision:
        reason = "Многосегментный бизнес: выбор меры подтверждён."
    elif multi_segment:
        reason = "Многосегментный бизнес: выбор меры не подтверждён."
    elif has_decision:
        reason = "Выбор меры подтверждён; многосегментность не объявлена."
    else:
        reason = "Многосегментность и подтверждённый выбор меры не объявлены."

    return Scope(multi_segment, segments, has_decision, reason)


def gate(profile) -> str | None:
    """Refuse valuation until a multi-segment scope choice is confirmed."""
    assessed = assess(profile)
    if not assessed.multi_segment or assessed.has_decision:
        return None

    names = [_segment_name(segment, ("name", "segment", "peer_kind"))
             for segment in assessed.segments]
    declared = ", ".join(str(name) if name is not None else str(segment)
                         for name, segment in zip(names, assessed.segments))
    return (
        f"многосегментный бизнес ({declared}): выберите меру levered "
        "(blended) или sotp с обоснованием и подтвердите scope; "
        "set scope … → confirm"
    )


def comps_cover_segments(segments: list, comps: list) -> bool:
    """Return whether every segment has a matching public/private or legacy comp."""
    if not segments:
        return True

    comp_segments = [_segment_name(comp, ("segment", "peer_kind", "name"))
                     for comp in (comps or [])]
    return all(
        (name := _segment_name(segment, ("name", "peer_kind", "segment")))
        is not None and name in comp_segments
        for segment in segments
    )


def has_head_comps(comps: list | None) -> bool:
    """Return whether at least one whole-company comp is declared."""
    return bool(comps)


def _segment_name(item, fields):
    if isinstance(item, dict):
        for field in fields:
            if item.get(field) is not None:
                return item[field]
        return None
    return item
