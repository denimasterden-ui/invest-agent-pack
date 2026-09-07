"""Полоса мультипликатора из сопоставимых компаний (SPC-008).

Полоса приходит из рынка, а не из головы модели. Сопоставимые берутся из
профиля, метрика — осмысленная для типа бизнеса: P/Bv при данном ROE для банка,
EV/Sales или EV/ARR для аренды мощности, forward P/E для производителя.

Возвращается не одно число, а полоса и позиция самой бумаги в ней: вопрос не
«какой мультипликатор правильный», а «дороже или дешевле сопоставимых и за
что». Полоса подаётся модели как доступный якорь класса «статистика
сопоставимых», и модель ссылается на неё вместо собственной прикидки.

Данные сопоставимых проверяются: если отчётность в одной валюте, а котировка
в другой, мультипликатор через них несопоставим — peer пропускается с
объяснением. Если чистых сопоставимых меньше двух, полоса не строится вовсе:
отказ честнее мусорного якоря.

Первоисточник — prototype_fork/peers.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re

from . import profile


# ── метрики сравнения ──────────────────────────────────────────────────────────

PBV_PER_ROE = "pbv_per_roe"
EV_PER_SALES = "ev_per_sales"
FORWARD_PE = "forward_pe"
SUM_OF_PARTS = "sum_of_parts"


@dataclass(frozen=True)
class PeerBand:
    """Полоса мультипликатора из comps профиля и её смысл."""
    kind: str                      # pbv_per_roe | ev_per_sales | forward_pe
    band: tuple | None             # (low, high) — полоса сопоставимых; None если не строится
    median: float | None           # медиана полосы
    peers: dict                    # тикер → {value, ...} — чистые сопоставимые
    skipped: dict                  # тикер → причина пропуска
    own: dict | None               # собственная метрика бумаги
    position: str | None = None    # «внутри полосы», «выше полосы на X%», «ниже полосы на X%»
    note: str | None = None        # почему полоса не построилась
    warnings: tuple = ()           # несопоставимые отчётные периоды и другие guard'ы


def build(prof, peer_data=None):
    """Полоса мультипликатора из comps профиля.

    peer_data: {ticker: info-словарь} — данные сопоставимых (поля как у market data).
    Если None, данные не переданы — полоса не строится.
    """
    if peer_data is None:
        peer_data = {}
    eff = prof.effective
    kind = eff.multiple_metric
    if not eff.comps or kind == SUM_OF_PARTS:
        return None
    # comps канона — структурные ({name, kind}); peer_data ключуется тикером,
    # поэтому сводим к имени. Реестровые comps уже строки — проходят как есть.
    comps = [c["name"] if isinstance(c, dict) else c for c in eff.comps]
    vals, skipped = {}, {}
    warnings = _fiscal_year_warnings(prof.ticker, comps, peer_data)
    for comp in comps:
        info = peer_data.get(comp)
        if not info:
            skipped[comp] = "нет данных"
            continue
        metric = _metric(comp, info, kind)
        if metric is None:
            skipped[comp] = "метрика недоступна"
        elif metric.get("skip"):
            skipped[comp] = metric["skip"]
        else:
            vals[comp] = metric
    if len(vals) < 2:
        return PeerBand(
            kind=kind, band=None, median=None, peers=vals, skipped=skipped,
            own=_own(prof.ticker, peer_data, kind),
            note="меньше двух сопоставимых с чистыми данными — "
                 "полоса не строится, нужна ручная нормализация",
            warnings=warnings)
    nums = sorted(v["value"] for v in vals.values())
    band = (nums[0], nums[-1])
    median = nums[len(nums) // 2]
    own = _own(prof.ticker, peer_data, kind)
    return PeerBand(kind=kind, band=band, median=median, peers=vals,
                    skipped=skipped, own=own,
                    position=_position(own, band), warnings=warnings)


def _fiscal_year_warnings(ticker, comps, peer_data):
    """Warn when forward estimates refer to fiscal years over a quarter apart."""
    own_end = _fiscal_month(peer_data.get(ticker, {}).get("fiscal_year_end"))
    if own_end is None:
        return ()
    out = []
    for comp in comps:
        peer_end = _fiscal_month(peer_data.get(comp, {}).get("fiscal_year_end"))
        if peer_end is None:
            continue
        delta = abs(own_end - peer_end)
        delta = min(delta, 12 - delta)
        if delta > 3:
            out.append(
                f"{comp}: fiscal year end отличается от {ticker} на {delta} мес.; "
                "forward P/E относится к несопоставимым периодам")
    return tuple(out)


def _fiscal_month(value):
    """Read an ISO date, MM-DD string, date, or Unix timestamp as a month."""
    if isinstance(value, datetime):
        return value.month
    if isinstance(value, date):
        return value.month
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.utcfromtimestamp(value).month
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        match = re.search(r"(?:^|\D)(0?[1-9]|1[0-2])[-/]\d{1,2}$", value)
        if match:
            return int(match.group(1))
        try:
            return date.fromisoformat(value[:10]).month
        except ValueError:
            return None
    return None


# ── метрики по типу бизнеса ────────────────────────────────────────────────────

def _metric(ticker, info, kind):
    if kind == PBV_PER_ROE:
        return _pbv_per_roe(ticker, info)
    if kind == EV_PER_SALES:
        return _ev_per_sales(ticker, info)
    if kind == FORWARD_PE:
        return _forward_pe(ticker, info)
    return None


def _pbv_per_roe(ticker, info):
    if not _fx_consistent(info):
        return {"skip": f"{info.get('financialCurrency')} против "
                        f"{info.get('currency')} — P/Bv несопоставим"}
    pb = info.get("priceToBook")
    roe = info.get("returnOnEquity")
    if not _is_number(pb) or not _is_number(roe) or roe <= 0:
        return None
    return {"value": pb / roe, "pb": pb, "roe": roe}


def _ev_per_sales(ticker, info):
    if not _fx_consistent(info):
        return {"skip": f"{info.get('financialCurrency')} против "
                        f"{info.get('currency')} — EV/Sales несопоставим"}
    ev = _enterprise_value(info)
    rev = info.get("totalRevenue")
    if not _is_number(ev) or not _is_number(rev) or rev <= 0:
        return None
    return {"value": ev / rev, "ev": ev, "rev": rev,
            "growth": info.get("revenueGrowth")}


def _forward_pe(ticker, info):
    v = info.get("forwardPE")
    if not _is_number(v) or v <= 0:
        return None
    return {"value": v, "eps": info.get("forwardEps")}


# ── валюта: согласованность котировки и отчётности ─────────────────────────────

def _fx_consistent(info):
    """Считается ли метрика в одной валюте.

    Цена берётся в валюте котировки, а balance sheet — в валюте отчётности,
    и priceToBook делит одно на другое без конверсии. Для ADR добавляется ещё
    и коэффициент представления. На таких данных якорь строить нельзя — лучше
    отказ, чем мусорное число.
    """
    fin = info.get("financialCurrency")
    quote = info.get("currency")
    return not (fin and quote and fin != quote)


# ── собственная метрика бумаги ─────────────────────────────────────────────────

def _own(ticker, peer_data, kind):
    info = peer_data.get(ticker)
    if not info:
        return None
    metric = _metric(ticker, info, kind)
    if metric is None or metric.get("skip"):
        return None
    return metric


# ── позиция бумаги относительно полосы ─────────────────────────────────────────

def _position(own, band):
    if own is None or band is None:
        return None
    own_val = own["value"]
    low, high = band
    if own_val < low:
        pct = (low - own_val) / low * 100
        return f"ниже полосы на {pct:.0f}%"
    if own_val > high:
        pct = (own_val - high) / high * 100
        return f"выше полосы на {pct:.0f}%"
    return "внутри полосы"


# ── вспомогательные ────────────────────────────────────────────────────────────

def _enterprise_value(info):
    cap = info.get("marketCap")
    if not _is_number(cap):
        return None
    debt = info.get("totalDebt") or 0
    cash = info.get("totalCash") or 0
    return cap + debt - cash


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)
