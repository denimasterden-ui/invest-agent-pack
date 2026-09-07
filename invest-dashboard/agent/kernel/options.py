"""Опционный сентимент из Yahoo Finance (yfinance) + собственный расчёт IV.

Зачем не голый yfinance: поле impliedVolatility у Yahoo ненадёжно (возвращает
1e-05 по неликвиду и округлённый мусор). Put/Call объёмы и OI у Yahoo надёжны —
их берём напрямую; а IV пересчитываем из mid-цены опциона готовым py_vollib
(референс-алгоритм Peter Jäckel «Let's be rational»).

Никакого резидентства/подписок — данные Yahoo публичные.

Отдаёт то же, что раньше отдавал IBKR-слой, чтобы entry_timing не менялся:
  {expiry, pcr_volume, pcr_oi, atm_iv, iv_skew, n_contracts}

Ограничение: свой IV считается из mid(bid,ask) — а он живой только в торговые
часы США. Вне сессии bid/ask=0 → откат на lastPrice (устаревшая, но ненулевая),
IV будет грубее. Put/Call и OI работают круглосуточно.

CLI-тест:  /usr/bin/python3 options.py NBIS
"""
from __future__ import annotations

import datetime as dt
import warnings

warnings.filterwarnings("ignore")

RISK_FREE = 0.045  # ~текущая безрисковая ставка US; для IV чувствительность низкая


def _median(xs):
    xs = sorted(v for v in xs if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _iv(price, spot, strike, t_years, flag):
    """IV из цены опциона через py_vollib. None при отсутствии решения."""
    if not price or price <= 0 or t_years <= 0:
        return None
    try:
        from py_vollib.black_scholes.implied_volatility import implied_volatility
        v = implied_volatility(price, spot, strike, t_years, RISK_FREE, flag)
        return v if 0.01 < v < 5 else None  # отсечь вырожденные решения
    except Exception:
        return None


def _row_price(row):
    """Цена опциона → (price, is_live). mid(bid,ask) если жив, иначе устаревшая lastPrice."""
    bid, ask = row.get("bid"), row.get("ask")
    if bid and ask and bid > 0 and ask > 0:
        return (bid + ask) / 2, True
    lp = row.get("lastPrice")
    return (lp if lp and lp > 0 else None), False


def options_summary(symbol: str, min_days: int = 14, band: float = 0.15) -> dict | None:
    """Опционный сентимент по ближайшей «нормальной» экспирации (>= сегодня+min_days)."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        exps = t.options
        if not exps:
            return None

        fi = t.fast_info
        spot = fi.get("last_price") or fi.get("lastPrice") or fi.get("regular_market_price")
        spot = float(spot) if spot else None
        if not spot:
            return None

        today = dt.date.today()
        target = None
        for e in exps:
            try:
                d = dt.datetime.strptime(e, "%Y-%m-%d").date()
            except ValueError:
                continue
            if (d - today).days >= min_days:
                target = e
                target_date = d
                break
        if target is None:
            target = exps[-1]
            target_date = dt.datetime.strptime(target, "%Y-%m-%d").date()

        oc = t.option_chain(target)
        calls, puts = oc.calls, oc.puts
        t_years = max((target_date - today).days, 1) / 365.0

        # Put/Call — прямо из Yahoo (надёжно)
        cv = float(calls["volume"].fillna(0).sum())
        pv = float(puts["volume"].fillna(0).sum())
        coi = float(calls["openInterest"].fillna(0).sum())
        poi = float(puts["openInterest"].fillna(0).sum())

        # IV считаем сами, только по ликвидным контрактам в полосе ±band вокруг spot
        lo, hi = spot * (1 - band), spot * (1 + band)
        atm_ivs, otm_put_ivs, otm_call_ivs = [], [], []
        atm_live = 0  # сколько ATM-вкладов пришло с живого mid (а не устаревшей lastPrice)

        for df, flag in ((calls, "c"), (puts, "p")):
            sub = df[(df["strike"] >= lo) & (df["strike"] <= hi)]
            for _, row in sub.iterrows():
                k = float(row["strike"])
                # ликвидность: есть объём или OI
                if not (row.get("volume", 0) or row.get("openInterest", 0)):
                    continue
                px, is_live = _row_price(row)
                iv = _iv(px, spot, k, t_years, flag)
                if iv is None:
                    continue
                if abs(k - spot) / spot <= 0.03:
                    atm_ivs.append(iv)
                    if is_live:
                        atm_live += 1
                if flag == "p" and k < spot * 0.97:
                    otm_put_ivs.append(iv)
                if flag == "c" and k > spot * 1.03:
                    otm_call_ivs.append(iv)

        atm_iv = _median(atm_ivs)
        put_iv, call_iv = _median(otm_put_ivs), _median(otm_call_ivs)
        skew = (put_iv - call_iv) if (put_iv is not None and call_iv is not None) else None

        return {
            "expiry": target,
            "pcr_volume": (pv / cv) if cv else None,
            "pcr_oi": (poi / coi) if coi else None,
            "atm_iv": atm_iv,
            "iv_skew": skew,
            "n_contracts": len(calls) + len(puts),
            "iv_source": ("computed" if (atm_iv and atm_live)
                          else "stale" if atm_iv else "unavailable"),
        }
    except Exception:
        return None


def snapshot(symbol: str) -> dict | None:
    """Совместимо со старым интерфейсом: {options: {...}}. None при полном провале."""
    opts = options_summary(symbol)
    if opts is None:
        return None
    return {"options": opts}


if __name__ == "__main__":
    import sys, json
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "NBIS"
    print(f"Опционный снимок {sym} (Yahoo + py_vollib)…")
    snap = snapshot(sym)
    print(json.dumps(snap, indent=2, ensure_ascii=False, default=str) if snap
          else "❌ Нет опционных данных (нет цепочки / нет сети).")
