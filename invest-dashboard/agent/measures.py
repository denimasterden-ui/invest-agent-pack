"""Trusted valuation arithmetic for measures selected by :mod:`agent.profile`.

Callers declare assumptions.  This module derives value bounds; it never
accepts model-declared bounds and never chooses a measure from financial data.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import physical_caps, profile

SENSITIVITY_BAND = 0.0075
TERMINAL_GROWTH_CEILING = 0.04
TERMINAL_MARGIN_CEILING = 0.40
TERMINAL_MULTIPLE_CEILING = 30.0


@dataclass(frozen=True)
class Result:
    ticker: str
    measure: str
    measure_reason: str
    corridor: tuple | None = None
    warnings: tuple = ()
    errors: tuple = ()
    detail: object = None

    def is_refusal(self):
        return bool(self.errors)


def calculate(ticker, assumptions, physical=None):
    """Calculate the Base corridor with the measure stored in the profile."""
    prof = profile.build(ticker)
    try:
        if prof.measure == profile.LEVERED:
            corridor = _levered(assumptions)
            scenarios = assumptions.get("scenarios", {})
        elif prof.measure in (profile.EV_REVENUE, profile.SOTP):
            owner = assumptions.get("core", assumptions)
            corridor = _ev_revenue(owner)
            scenarios = owner.get("scenarios", {})
            if prof.measure == profile.SOTP:
                corridor = _add_stakes(corridor, assumptions, owner["shares"])
        else:
            raise ValueError(f"мера {prof.measure} этим модулем не считается")
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        return Result(ticker, prof.measure, prof.measure_reason,
                      errors=(str(exc),))

    driver_facts = dict(physical or {})
    # Введённые вручную драйверы из профиля участвуют в проверке потолков
    # автоматически: вызывающий не обязан помнить, какие пробелы закрыты.
    for name, fact in (prof.driver_facts or {}).items():
        driver_facts.setdefault(name, fact.get("value"))
    owner = assumptions.get("core", assumptions)
    if "revenue_base" in owner:
        driver_facts.setdefault("revenue_base", owner["revenue_base"])
    if "base_year" in assumptions:
        driver_facts.setdefault("base_year", assumptions["base_year"])
    warnings = list(physical_caps.check(prof, scenarios, driver_facts))
    if prof.measure == profile.LEVERED:
        debt_warning = _levered_debt_warning(assumptions)
        if debt_warning is not None:
            warnings.append(debt_warning)
    return Result(ticker, prof.measure, prof.measure_reason, corridor,
                  tuple(warnings), detail=assumptions)


def _levered_debt_warning(data):
    """Flag debt-tail blindness without changing levered DCF arithmetic."""
    net_debt = data.get("net_debt")
    scenario = data.get("scenarios", {}).get("base", {})
    fcf = scenario.get("base_value", data.get("fcf_base"))
    if not (_positive(net_debt) and _positive(fcf)):
        return None
    leverage = net_debt / fcf
    threshold = profile.NET_DEBT_TO_FCF_WARNING_THRESHOLD
    if leverage <= threshold:
        return None
    return physical_caps.Warning(
        "levered_debt_tail",
        f"net_debt/FCF {leverage:.1f}× > порога {threshold:g}×: "
        "повышенный рефи-риск; levered-мера слепа к долговому tail")


def _levered(data):
    scenario = _base(data)
    # Объявленный вперёд уровень побеждает; fcf_base читается только тогда,
    # когда его нет, — иначе dict.get посчитал бы default всегда и требовал
    # бы поле, которое base_value призван заменить.
    fcf = (_number(scenario, "base_value") if "base_value" in scenario
           else _positive_number(data, "fcf_base", signed=True))
    shares = _positive_number(data, "shares")
    terminal_growth = _number(scenario, "terminal_growth")
    if terminal_growth > TERMINAL_GROWTH_CEILING:
        raise ValueError(
            f"terminal_growth {terminal_growth:.1%} выше долгосрочного "
            f"макропотолка {TERMINAL_GROWTH_CEILING:.1%}")
    return _band(lambda rate: _levered_vps(
        fcf, scenario["growth_path"], rate,
        terminal_growth, shares), scenario)


def _levered_vps(fcf, growth_path, rate, terminal_growth, shares):
    if rate <= terminal_growth:
        raise ValueError("discount_rate должен быть выше terminal_growth")
    pv = 0.0
    for year, growth in enumerate(_path(growth_path), 1):
        fcf *= 1 + growth
        pv += fcf / (1 + rate) ** year
    terminal = fcf * (1 + terminal_growth) / (rate - terminal_growth)
    return (pv + terminal / (1 + rate) ** len(growth_path)) / shares


def _ev_revenue(data):
    scenario = _base(data)
    revenue = (_number(scenario, "base_value") if "base_value" in scenario
               else _positive_number(data, "revenue_base"))
    shares = _positive_number(data, "shares")
    net_cash = _number(data, "net_cash")
    margin = _number(scenario, "terminal_fcf_margin")
    multiple = _number(scenario, "terminal_fcf_multiple")
    if not 0 < margin <= TERMINAL_MARGIN_CEILING:
        raise ValueError("terminal_fcf_margin вне (0%, 40%]")
    if not 0 < multiple <= TERMINAL_MULTIPLE_CEILING:
        raise ValueError("terminal_fcf_multiple вне (0, 30x]")
    path = _path(scenario["growth_path"])

    def value(rate):
        terminal_revenue = revenue
        for growth in path:
            terminal_revenue *= 1 + growth
        ev = terminal_revenue * margin * multiple / (1 + rate) ** len(path)
        return (ev + net_cash) / shares
    return _band(value, scenario)


def _add_stakes(corridor, data, shares):
    stakes = data.get("stakes")
    if not isinstance(stakes, list) or not stakes:
        raise ValueError("sotp требует непустой список stakes")
    value = 0.0
    for stake in stakes:
        ownership = _number(stake, "ownership_pct")
        if not 0 < ownership <= 1:
            raise ValueError("ownership_pct должен быть в (0, 1]")
        valuation = _positive_number(stake["entity_valuation"], "base")
        value += ownership * valuation / shares
    return round(corridor[0] + value, 2), round(corridor[1] + value, 2)


def _band(fn, scenario):
    rate = _number(scenario, "discount_rate")
    if rate <= SENSITIVITY_BAND:
        raise ValueError("discount_rate слишком мал для sensitivity band")
    low, high = fn(rate + SENSITIVITY_BAND), fn(rate - SENSITIVITY_BAND)
    return round(min(low, high), 2), round(max(low, high), 2)


def _base(data):
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, dict) or "base" not in scenarios:
        raise ValueError("нужен сценарий base с объявленными предпосылками")
    return scenarios["base"]


def _path(value):
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("growth_path должен быть непустым списком")
    if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v <= -1
           for v in value):
        raise ValueError("growth_path содержит недопустимый темп")
    return value


def _number(data, name):
    value = data.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} должен быть числом")
    return value


def _positive_number(data, name, signed=False):
    value = _number(data, name)
    if not signed and value <= 0:
        raise ValueError(f"{name} должен быть положительным")
    return value


def _positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value > 0)
