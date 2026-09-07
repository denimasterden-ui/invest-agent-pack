"""Non-blocking physical growth checks for profile-owned measures.

The checks consume declared driver facts.  Missing facts mean that a check is
not available; a breach is a warning and never a valuation refusal.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Warning:
    code: str
    message: str


def check(prof, scenarios, physical=None):
    """Return numeric warnings for the effective business in *prof*."""
    physical = physical or {}
    base = (scenarios or {}).get("base", {})
    kind = prof.effective.business_kind
    out = []

    if "вычислительной мощности" in kind:
        out.extend(_capacity(base, physical))
    elif "AI-сегментом" in kind:
        out.extend(_backlog(base, physical))
    elif "памяти" in kind:
        out.extend(_cycle(physical))
    return tuple(out)


def _capacity(scenario, data):
    required = ("revenue_base", "revenue_per_mw", "contracted_gw")
    if any(not _positive(data.get(k)) for k in required):
        return []
    path = scenario.get("growth_path")
    if not _growth_path(path):
        return []

    revenue = data["revenue_base"]
    for growth in path:
        revenue *= 1 + growth
    required_gw = revenue / data["revenue_per_mw"] / 1000
    current_gw = data["revenue_base"] / data["revenue_per_mw"] / 1000
    out = []
    if required_gw > data["contracted_gw"]:
        out.append(Warning(
            "capacity_total",
            f"траектория требует {required_gw:.1f} ГВт > "
            f"законтрактованных {data['contracted_gw']:.1f} ГВт "
            f"при {data['revenue_per_mw']/1e6:.1f} млн выручки на МВт"))
    pace = (required_gw - current_gw) / len(path)
    if _positive(data.get("build_rate_gw")) and pace > data["build_rate_gw"]:
        out.append(Warning(
            "capacity_pace",
            f"траектория требует ввода {pace:.1f} ГВт/год > объявленных "
            f"{data['build_rate_gw']:.1f} ГВт/год"))
    return out


def _backlog(scenario, data):
    backlog, cycle = data.get("backlog"), data.get("delivery_cycle_years")
    if not (_positive(backlog) and _positive(cycle)):
        return []
    planned = data.get("segment_revenue_next_year")
    if planned is None:
        return []
    ceiling = backlog / cycle
    if planned > ceiling:
        return [Warning(
            "backlog_delivery",
            f"выручка сегмента {planned:,.0f} за год > {ceiling:,.0f}, "
            f"которые backlog {backlog:,.0f} допускает при цикле поставки "
            f"{cycle:g} года")]
    return []


def _cycle(data):
    position = str(data.get("cycle_position", "")).lower()
    year = str(data.get("base_year", "объявленный год"))
    if position in ("peak", "пик") or "peak" in year.lower() or "пик" in year.lower():
        return [Warning(
            "cycle_peak_base",
            f"{year} помечен как пик цикла — нормализуйте базу по "
            "историческому размаху, не компаундите пик молча")]
    return []


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _growth_path(value):
    return isinstance(value, (list, tuple)) and bool(value) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and v > -1
        for v in value)
