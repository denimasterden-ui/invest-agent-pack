"""Pure stock signals, book-prism catalog, and valuation-measure gate."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any


_CATALOG_PATH = (Path(__file__).resolve().parents[1] /
                 "knowledge" / "classifiers" / "book_prisms.md")
_ENTRY = re.compile(r"^\*\*(?P<name>.+?)\*\*\s*$")
_FIELD = re.compile(r"^- (?P<label>Признаки|Предлагает|Не применим):\s*(?P<text>.*)$")


@dataclass(frozen=True)
class Prism:
    """One analyst lens, as written in ``book_prisms.md``."""

    name: str
    signs: str
    applies: str
    offers: str


@dataclass(frozen=True)
class Justification:
    """Whether the selected measure has a confirmed prism rationale."""

    measure_justified: bool
    reason: str


def signals(profile: dict, facts: dict) -> dict:
    """Calculate factual prism inputs without choosing an applicable prism.

    FCF stability means sign stability across an explicitly supplied history;
    a single observation cannot establish stability.  Missing inputs remain
    ``None`` rather than being inferred from the type of business.
    """
    profile = profile if isinstance(profile, dict) else {}
    facts = facts if isinstance(facts, dict) else {}

    fcf = _first_present(facts, "fcf", "free_cash_flow", "free_cashflow")
    fcf_values = _numbers(fcf)
    cash_flow = _mapping(_first_present(
        facts, "cash_flow", "cashflow", "cash-flow"))
    if not fcf_values:
        opcf = _numbers(_first_present(
            cash_flow, "opcf", "operating_cash_flow", "operating_cashflow"))
        capex = _numbers(_first_present(
            cash_flow, "capex", "capital_expenditure",
            "capital_expenditures"))
        fcf_values = _subtract_series(opcf, capex)
    current_fcf = fcf_values[-1] if fcf_values else None

    net_income = _latest_number(facts.get("net_income"))
    sbc = _latest_number(_first_present(
        facts, "sbc", "stock_based_compensation"))
    if sbc is None:
        sbc = _latest_number(_first_present(
            cash_flow, "sbc", "stock_based_compensation"))
    sbc_ratio = None
    if _is_number(sbc) and _is_number(net_income) and net_income != 0:
        sbc_ratio = sbc / net_income

    if "has_stakes" in facts:
        has_stakes = bool(facts["has_stakes"])
    elif "stakes" in facts:
        has_stakes = bool(facts["stakes"])
    elif "has_stakes" in profile:
        has_stakes = bool(profile["has_stakes"])
    else:
        has_stakes = None

    return {
        "fcf_sign": _sign(current_fcf),
        "fcf_stable": _stable_sign(fcf_values),
        "sbc_ratio": sbc_ratio,
        "net_cash_sign": _net_cash_sign(facts),
        "has_stakes": has_stakes,
        "research_type": profile.get("research_type"),
    }


def catalog() -> list[Prism]:
    """Parse the project book-prism reference into catalog records."""
    lines = _CATALOG_PATH.read_text(encoding="utf-8").splitlines()
    records: list[Prism] = []
    current: dict[str, str] | None = None
    active_field: str | None = None
    field_names = {"Признаки": "signs", "Не применим": "applies",
                   "Предлагает": "offers"}

    def finish() -> None:
        if current is not None:
            records.append(Prism(
                current["name"], current.get("signs", ""),
                current.get("applies", ""), current.get("offers", "")))

    for line in lines:
        heading = _ENTRY.match(line)
        if heading:
            finish()
            current = {"name": heading.group("name").strip()}
            active_field = None
            continue
        if current is None:
            continue
        field = _FIELD.match(line)
        if field:
            active_field = field_names[field.group("label")]
            text = field.group("text").strip()
            if field.group("label") == "Не применим":
                text = f"Не применим: {text}"
            current[active_field] = text
        elif active_field and line.startswith("  "):
            current[active_field] = (current[active_field] + " " +
                                     line.strip()).strip()
        elif not line.strip():
            active_field = None
    finish()
    return records


def justified(profile: dict) -> Justification:
    """Assess only explicit prism justification stored in profile scope."""
    scope = profile.get("scope", {}) if isinstance(profile, dict) else {}
    scope = scope if isinstance(scope, dict) else {}
    reason = scope.get("reason") or ""
    return Justification(bool(reason) and bool(scope.get("confirmed_by")),
                         reason)


def gate(profile: dict, has_info: bool) -> str | None:
    """Refuse blind prism selection or an unconfirmed measure rationale."""
    if not has_info:
        return "призмы вслепую: сначала соберите факты и материал"
    if not justified(profile).measure_justified:
        return ("мера не обоснована призмами: cli prisms → выбери меру → "
                "set scope → confirm")
    return None


def _first_present(values: dict, *names: str) -> Any:
    for name in names:
        if name in values:
            return values[name]
    return None


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _latest_number(value: Any) -> float | None:
    values = _numbers(value)
    return values[-1] if values else None


def _subtract_series(left: list[float], right: list[float]) -> list[float]:
    """Compute FCF only where OPCF and capex observations can be paired."""
    if not left or not right:
        return []
    if len(left) == 1 and len(right) == 1:
        return [left[0] - right[0]]
    if len(left) != len(right):
        return []
    return [opcf - capex for opcf, capex in zip(left, right)]


def _net_cash_sign(facts: dict) -> int | None:
    net_cash = _latest_number(facts.get("net_cash"))
    if net_cash is not None:
        return _sign(net_cash)
    net_debt = _latest_number(facts.get("net_debt"))
    return _sign(-net_debt) if net_debt is not None else None


def _numbers(value: Any) -> list[float]:
    if _is_number(value):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if _is_number(item)]
    return []


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _sign(value: Any) -> int | None:
    if not _is_number(value):
        return None
    return (value > 0) - (value < 0)


def _stable_sign(values: list[float]) -> bool | None:
    if len(values) < 2:
        return None
    return len({_sign(value) for value in values}) == 1
