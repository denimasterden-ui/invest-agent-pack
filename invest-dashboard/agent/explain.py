"""Человекочитаемый рендер детализации канона (без новых утверждений).

Машинный слой: переводит сохранённые числа (assumptions/corridor/форки) в
читаемые строки, ничего не досчитывая и не додумывая. Прозу для человека
поверх этого пишет аналитик (Claude Code) + /humanizer — здесь только факты.
"""
from __future__ import annotations


def _m(value):
    """Денежное в млн, аккуратно."""
    try:
        return f"{value/1e6:,.0f}M"
    except (TypeError, ValueError):
        return "—"


def baseline_lines(measure: str, assumptions: dict | None,
                   corridor) -> list[str]:
    """Как из сохранённых входов вышел коридор baseline."""
    a = assumptions or {}
    out: list[str] = []
    if measure == "ddm_ri":
        d = a.get("detail") or {}
        if d:
            rg = (d.get("r") or 0) - (d.get("g") or 0)
            out.append(f"DDM {d.get('ddm'):.2f} — дивиденд вперёд {d.get('dps'):.2f} "
                       f"при r−g {rg:.1%}")
            out.append(f"P/Bv {d.get('pbv'):.2f} — ROE {d.get('roe'):.1%} → "
                       f"множитель {d.get('pbv_multiple'):.2f}× × капитал "
                       f"{d.get('book_value_per_share'):.2f}/акц")
            out.append(f"ставка {d.get('r'):.1%} · рост {d.get('g'):.1%} "
                       f"(валюта отчётности)")
        else:
            out.append(f"ставка {a.get('rate')} · рост {a.get('growth')} "
                       f"— расчётный detail не сохранён (прогон до обновления)")
    elif measure in ("ev_revenue", "sotp"):
        # core лежит под ключом core (или в самом словаре), терминал —
        # маржа × множитель; sotp сверху добавляет доли и кэш.
        core = a.get("core", a)
        sc = (core.get("scenarios") or {}).get("base", {})
        rev, sh, nc = (core.get("revenue_base"), core.get("shares"),
                       core.get("net_cash"))
        gp, r = sc.get("growth_path"), sc.get("discount_rate")
        margin, mult = (sc.get("terminal_fcf_margin"),
                        sc.get("terminal_fcf_multiple"))
        if rev is not None:
            line = f"ядро (EV/Revenue): выручка база {rev:g}"
            if sh:
                line += f" · акций {sh:g}"
            out.append(line)
        if margin is not None and mult is not None:
            out.append(f"терминал: FCF-маржа {margin:.1%} × множитель {mult:g}×")
        if gp:
            out.append("траектория роста: "
                       + ", ".join(f"{g:+.0%}" for g in gp))
        if r is not None:
            out.append(f"ставка дисконта {r:.1%}")
        if nc is not None:
            out.append(f"+ чистый кэш {nc:g}")
        for st in (a.get("stakes") or []):
            own = st.get("ownership_pct")
            val = (st.get("entity_valuation") or {}).get("base")
            if own is not None and val is not None:
                out.append(f"+ доля {own:.0%} × оценка {val:g}")
    else:  # levered — DCF из объявленных предпосылок
        fcf, sh = a.get("fcf_base"), a.get("shares")
        sc = (a.get("scenarios") or {}).get("base", {})
        gp, r, tg = (sc.get("growth_path"), sc.get("discount_rate"),
                     sc.get("terminal_growth"))
        if fcf and sh:
            out.append(f"FCF база {_m(fcf)} · акций {sh/1e6:.1f}M · "
                       f"FCF/акцию ~{fcf/sh:.2f}")
        if gp:
            out.append("траектория роста: "
                       + ", ".join(f"{g:+.0%}" for g in gp))
        if r is not None:
            out.append(f"ставка дисконта {r:.1%} · терминальный рост "
                       f"{tg:.1%}" if tg is not None else f"ставка дисконта {r:.1%}")
    if corridor:
        out.append(f"→ справедливо {corridor[0]:g}–{corridor[1]:g} за акцию")
    return out


def verdict_line(corridor, price) -> str | None:
    """Цена против коридора — единственная строка, требующая внешней цены."""
    if not corridor or price is None:
        return None
    low, high = corridor
    mid = (low + high) / 2
    gap = (price - mid) / mid
    if price > high:
        where = f"выше верхней границы на {(price-high)/high:+.0%}"
    elif price < low:
        where = f"ниже нижней границы на {(price-low)/low:+.0%}"
    else:
        where = "внутри коридора"
    return f"цена {price:g} — {where} (к середине {gap:+.0%})"
