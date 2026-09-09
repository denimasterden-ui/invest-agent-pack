#!/usr/bin/env python3
"""entry-timing — метод «локального дна»: воронка макро → сектор → бумага → наши сценарии.

Собирает объективный количественный слой (техника тикера + макро-индексы + peers через
yfinance) и наши 3 сценария из инвест-дашборда, опционально свежий Perplexity-brief, и
прогоняет через Burry-аналитик по методу-воронке (task_entry.md). Выдаёт вердикт по 4
слоям + решающее правило входа + R/R.

Использование:
  /usr/bin/python3 entry_timing.py NBIS --peers CRWV [--no-web] [--max-tokens 6000]
"""
from __future__ import annotations

import sys, os, argparse, warnings
warnings.filterwarnings("ignore")

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = os.path.normpath(os.path.join(SKILL_DIR, "..", "..", "..", "invest-dashboard"))
sys.path.insert(0, DASHBOARD)

from agent.kernel.tickers import TICKERS_BY_KEY, resolve                     # noqa: E402
from agent.kernel import store_client                                        # noqa: E402
try:
    from agent.kernel import options as options_data                       # noqa: E402
except Exception:
    options_data = None

# Макро-«светофоры»: широкий рынок, tech, сектор полупроводников.
# VIX/SKEW/VVIX/F&G — в отдельном сентимент-блоке (_fmt_sentiment), с перцентилем.
MACRO = {"^GSPC": "S&P 500", "^IXIC": "Nasdaq Comp", "^SOX": "PHLX Semis"}


def _tech(symbol: str) -> dict:
    """Технический снимок символа через yfinance: цена, DMA50/200, RSI14, объём, импульс."""
    try:
        import yfinance as yf
        h = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=True)
        if h.empty:
            return {}
        close, vol = h["Close"], h["Volume"]
        last = float(close.iloc[-1])
        dma50  = float(close.tail(50).mean())
        dma200 = float(close.tail(200).mean()) if len(close) >= 200 else None
        delta = close.diff()
        ag = delta.clip(lower=0).rolling(14).mean().iloc[-1]
        al = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
        rsi = 100 - 100 / (1 + ag / al) if al and al > 0 else 100.0
        chg5  = (last / float(close.iloc[-6])  - 1) * 100 if len(close) > 6  else None
        chg20 = (last / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else None
        vol_r = float(vol.iloc[-1]) / float(vol.tail(20).mean()) if vol.tail(20).mean() else None
        hi52, lo52 = float(close.max()), float(close.min())
        return {"last": last, "dma50": dma50, "dma200": dma200, "rsi": rsi,
                "chg5": chg5, "chg20": chg20, "vol_r": vol_r, "hi52": hi52, "lo52": lo52}
    except Exception as e:
        return {"error": str(e)}


def _fmt_tech(name: str, t: dict) -> str:
    if not t or "error" in t:
        return f"  {name}: н/д ({t.get('error','')})"
    def p(x, s=""): return f"{x:.1f}{s}" if x is not None else "н/д"
    d50 = (t["last"]/t["dma50"]-1)*100 if t.get("dma50") else None
    d200 = (t["last"]/t["dma200"]-1)*100 if t.get("dma200") else None
    return (f"  {name}: {p(t['last'])} | vs50DMA {p(d50,'%')} | vs200DMA {p(d200,'%')} | "
            f"RSI {p(t['rsi'])} | 5д {p(t['chg5'],'%')} | 20д {p(t['chg20'],'%')} | "
            f"объём×{p(t['vol_r'])} | 52н {p(t['lo52'])}–{p(t['hi52'])}")


def _level_pct(symbol: str) -> tuple:
    """Последнее значение индекса-уровня + его перцентиль за год (0–100)."""
    try:
        import yfinance as yf
        c = yf.Ticker(symbol).history(period="1y", interval="1d")["Close"].dropna()
        if c.empty:
            return None, None
        last = float(c.iloc[-1])
        pct = float((c < last).mean() * 100)
        return last, pct
    except Exception:
        return None, None


def _cnn_fear_greed() -> tuple:
    """CNN Fear&Greed (score, rating) — неофициальный эндпоинт, браузерный UA.

    Хрупко (Cloudflare/сеть); при любом сбое — (None, None), слой деградирует
    тихо, а не валит сбор.
    """
    try:
        import requests
        r = requests.get(
            "https://production.dataviz.cnn.com/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=8)
        fg = r.json().get("fear_and_greed", {})
        return round(float(fg["score"])), str(fg.get("rating") or "")
    except Exception:
        return None, None


def _fmt_sentiment() -> str:
    """Слой 1 — сентимент/позиционирование рынка (контрарно, сигнал на экстремумах).

    VIX (реализованный страх) + SKEW (спрос на крашевый хедж) + VVIX (неопределённость
    самой волы) — уровень и перцентиль за год; CNN F&G — единый композит толпы.
    Низкий VIX + высокий SKEW = благодушие сверху, хедж крашей снизу.
    """
    def rank(pct):
        if pct is None: return ""
        if pct >= 80: return " ↑экстремум"
        if pct <= 20: return " ↓экстремум"
        return ""
    lines = ["=== Слой 1 — СЕНТИМЕНТ/ПОЗИЦИОНИРОВАНИЕ (контрарно, сигнал на экстремумах) ==="]
    for sym, nm in (("^VIX", "VIX страх"), ("^SKEW", "SKEW крашевый хедж"),
                    ("^VVIX", "VVIX вола-волы")):
        last, pct = _level_pct(sym)
        if last is None:
            lines.append(f"  {nm}: н/д")
        else:
            lines.append(f"  {nm}: {last:.1f} | перцентиль-1г {pct:.0f}%{rank(pct)}")
    score, rating = _cnn_fear_greed()
    if score is None:
        lines.append("  CNN Fear&Greed: н/д (неофиц. эндпоинт недоступен)")
    else:
        extreme = " ← ЭКСТРЕМУМ" if score <= 25 or score >= 75 else ""
        lines.append(f"  CNN Fear&Greed: {score}/100 · {rating}{extreme}")
    lines.append("  (F&G включает VIX/put-call/моментум — не считать компоненты "
                 "отдельными голосами; сигнал контрарный и в основном на экстремумах)")
    return "\n".join(lines)


def _volume_depth(symbol: str, days: int = 15) -> dict:
    """Углубление объёма: распределение/накопление, сохнет ли объём, был ли капит-день.

    Заостряет гейт капитуляции — отличает истощение продавца (объём сохнет = близко дно)
    от активного сброса (объём растёт на падении = нож летит).
    """
    try:
        import yfinance as yf
        h = yf.Ticker(symbol).history(period="3mo", interval="1d", auto_adjust=True)
        if h.empty or len(h) < 21:
            return {}
        close, vol = h["Close"], h["Volume"]
        v20 = float(vol.tail(20).mean())
        v5 = float(vol.tail(5).mean())
        # up/down volume за последние `days` баров
        tail = h.tail(days)
        d = tail["Close"].diff()
        up_vol = float(tail["Volume"][d > 0].sum())
        dn_vol = float(tail["Volume"][d < 0].sum())
        ud = (up_vol / dn_vol) if dn_vol else None
        # OBV-наклон за 20 баров (накопление/распределение)
        step = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
        obv = (step * vol).tail(20).cumsum()
        obv_up = bool(obv.iloc[-1] > 0)
        # капитуляционный день в последних 10: объём ×2+ при падении ≥3%
        last10 = h.tail(10)
        pchg = last10["Close"].pct_change()
        cap = bool(((last10["Volume"] >= 2 * v20) & (pchg <= -0.03)).any())
        return {"ud": ud, "vtrend": (v5 / v20) if v20 else None,
                "obv_up": obv_up, "cap_day": cap}
    except Exception:
        return {}


def _fmt_vol_depth(vd: dict) -> str:
    if not vd:
        return ""
    def p(x, d=2): return f"{x:.{d}f}" if isinstance(x, (int, float)) else "н/д"
    ud = vd.get("ud")
    ud_note = ("распределение (продавец доминирует)" if (ud and ud < 0.7)
               else "накопление (покупатель)" if (ud and ud > 1.3) else "баланс")
    vt = vd.get("vtrend")
    vt_note = ("сохнет → истощение продавца" if (vt and vt < 0.8)
               else "нарастает → активный сброс" if (vt and vt > 1.2) else "ровный")
    return ("=== Слой 3 доп. — ГЛУБИНА ОБЪЁМА ===\n"
            f"  Up/Down vol (15д): {p(ud)} → {ud_note}\n"
            f"  Объём 5д/20д: ×{p(vt)} → {vt_note}\n"
            f"  OBV-тренд (20д): {'накопление ↑' if vd.get('obv_up') else 'распределение ↓'} | "
            f"Капит-день (10д): {'ДА (×2+ на −3%+)' if vd.get('cap_day') else 'нет'}")


def _spot(symbol: str):
    """Текущая цена одним лёгким вызовом — нужна шагу 0 до сбора остальных данных."""
    try:
        import yfinance as yf
        return float(yf.Ticker(symbol).fast_info.last_price)
    except Exception:
        return None


def _scenario_candidates(key: str, spot=None, limit: int = 5) -> list:
    """Слой 4 из канона (v2): последний прогon — baseline=Base + форки bull/bear.

    В v2 сценарии живут в общем каноне на сервере (store_client.get_scenarios):
    один последний прогон на тикер, а не история многих строк. Base = коридор
    базовой линии, Bear/Bull = коридоры соответствующих форков. Возвращаем список
    из ≤1 кандидата, чтобы шаг 0 и форматтеры остались единообразны.

    gap — Base-mid против ТЕКУЩЕЙ цены (правило CLAUDE.md: ≳1.5× → сначала
    recheck; по нему шаг 0 и блокирует).
    """
    try:
        sc = store_client.get_scenarios(key)
    except Exception:
        return []
    base = (sc or {}).get("baseline")
    if not base or not base.get("corridor"):
        return []
    forks = {f.get("label"): f for f in ((sc or {}).get("forks") or [])}
    lo, hi = base["corridor"]
    base_mid = (lo + hi) / 2
    ts = str(base.get("run_at") or "")
    return [{
        "ts": ts, "base": base["corridor"], "measure": base.get("measure"),
        "bear": (forks.get("bear") or {}).get("corridor"),
        "bull": (forks.get("bull") or {}).get("corridor"),
        "base_mid": base_mid, "spot": spot,
        "gap": (max(spot / base_mid, base_mid / spot) if spot and base_mid else None),
        "age_days": _age_days(ts),
    }]


def _age_days(ts: str) -> int | None:
    from datetime import date
    try:
        y, m, d = (int(x) for x in ts[:10].split("-"))
        return (date.today() - date(y, m, d)).days
    except Exception:
        return None


def _corr(c):
    return f"{c[0]:.1f}–{c[1]:.1f}" if c else "н/д"


def _fmt_candidates(cands: list) -> str:
    """Человекочитаемый список прогонов — что взять как опору слоя 4."""
    lines = []
    for i, c in enumerate(cands, 1):
        flags = []
        if c["gap"] and c["gap"] >= 1.5:
            flags.append(f"⚠️ НЕ ОПОРА: Base-mid {c['base_mid']:.1f} vs текущая "
                         f"{c['spot']:.1f} = {c['gap']:.1f}×")
        if c["age_days"] is not None and c["age_days"] > 5:
            flags.append(f"⏳ {c['age_days']}д")
        lines.append(
            f"  [{i}] {c['ts'][:16]} ({c.get('measure') or '?'}) — "
            f"Bear {_corr(c['bear'])} | Base {_corr(c['base'])} | Bull {_corr(c['bull'])}\n"
            f"      " + (" | ".join(flags) if flags else "опора годна"))
    return "\n".join(lines)


def _scenarios_ctx(cand: dict) -> str:
    """Слой 4 для промпта — опора, а не гейт (см. task_entry.md)."""
    out = (f"Наша оценка из канона от {cand['ts'][:10]} (мера {cand.get('measure') or '?'}):\n"
           f"  🔴 Bear (форк): {_corr(cand['bear'])}\n"
           f"  🔵 Base (базовая линия): {_corr(cand['base'])}\n"
           f"  🟢 Bull (форк): {_corr(cand['bull'])}\n"
           f"  Возраст оценки: {cand['age_days']} дн.")
    if cand["gap"] and cand["gap"] >= 1.5:
        out += (f"\n  ⚠️ Base-mid расходится с ТЕКУЩЕЙ ценой в {cand['gap']:.1f}× — "
                "якорь ненадёжен. Слой 4 использовать только как грубую рамку, "
                "вес решения перенести на слои 1–3.5.")
    return out


def _options_ctx(symbol: str) -> str:
    """Слой 3.5 — опционный сентимент (Yahoo put/call+OI + свой IV через py_vollib).

    Тихий фолбэк: нет цепочки/сети/модуля → пустая строка, метод идёт на yfinance-технике.
    """
    if options_data is None:
        return ""
    try:
        snap = options_data.snapshot(symbol)
    except Exception:
        return ""
    o = (snap or {}).get("options")
    if not o:
        return ""

    def p(x, s="", d=1):
        return f"{x:.{d}f}{s}" if isinstance(x, (int, float)) else "н/д"

    lines = ["=== Слой 3.5 — ОПЦИОННЫЙ СЕНТИМЕНТ (Yahoo + py_vollib) ==="]
    lines.append(
        f"  Эксп {o.get('expiry','?')} ({o.get('n_contracts','?')} контр.): "
        f"put/call vol {p(o.get('pcr_volume'),'',2)} | put/call OI {p(o.get('pcr_oi'),'',2)}")
    iv = o.get("atm_iv")
    if iv:
        note = "" if o.get("iv_source") == "computed" else "  (⚠ рынок закрыт — IV грубый)"
        lines.append(
            f"  ATM IV {p(iv*100,'%')} | IV-skew {p((o.get('iv_skew') or 0)*100,'п.п.')}{note}")
    lines.append(
        "    (P/C > 1 = преобладают путы/хедж/страх; P/C < 0.7 = благодушие; "
        "IV-skew > 0 = крашевый put-скью; ATM IV — дорога ли премия)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="entry-timing: воронка локального дна")
    ap.add_argument("ticker", help="Тикер (ключ или алиас), напр. NBIS")
    ap.add_argument("--peers", default="", help="Сектор-peers через запятую, напр. CRWV,SMCI")
    ap.add_argument("--scenario", type=int, default=None,
                    help="Какой прогон взять опорой слоя 4 (1 = самый свежий). "
                         "Без него скрипт останавливается, если верхний прогон помечен")
    ap.add_argument("--note", default="",
                    help="ПРОВЕРЯЕМЫЕ факты в слои 1-3: то, чего нет в yfinance и не "
                         "факт, что вытащит Perplexity (срыв сроков стройки, "
                         "раскрытая позиция из филинга). Только то, что можно "
                         "перепроверить по источнику")
    ap.add_argument("--hypothesis", default="",
                    help="Непроверенные допущения (напр. «фонд X ещё не закончил "
                         "разгрузку»). Модель обязана пометить их как гипотезы и НЕ "
                         "строить на них гейты и триггеры входа")
    ap.add_argument("--max-age", type=int, default=5,
                    help="Сколько дней оценка считается свежей (по умолчанию 5)")
    args = ap.parse_args()

    entry = resolve(args.ticker) or TICKERS_BY_KEY.get(args.ticker.upper())
    key = entry["key"] if entry else args.ticker.upper()
    yf_symbol = (entry or {}).get("yf") or key

    # Шаг 0 — ДО дорогих вызовов (Perplexity + analyst): годна ли опора слоя 4.
    # Битый или протухший верхний прогон — не повод молча пересчитывать бумагу:
    # ниже может лежать валидная оценка. Выбор за человеком, скрипт лишь помечает.
    cands = _scenario_candidates(key, spot=_spot(yf_symbol))
    if not cands:
        print(f"⚠️  Наших сценариев по {key} нет — слой 4 будет пустым, "
              "воронка пойдёт на рыночной статистике (слои 1–3.5).")
        cand = None
    else:
        if args.scenario:
            cand = cands[min(args.scenario, len(cands)) - 1]
        else:
            top = cands[0]
            bad = (top["gap"] and top["gap"] >= 1.5) or \
                  (top["age_days"] is not None and top["age_days"] > args.max_age)
            if bad:
                print(f"\n⛔ Шаг 0: верхний прогон по {key} непригоден как опора "
                      f"(брак или протух). Выбери, на что опираться:\n")
                print(_fmt_candidates(cands))
                print(f"\n   Запусти повторно с --scenario N (напр. --scenario 2),"
                      f"\n   либо сделай свежий recheck этой бумаги, если ни один не годится."
                      f"\n   Данные слоёв ещё не собирались.\n")
                return 2
            cand = top

    print(f"→ Сбор данных для {key}…", flush=True)
    # Слой 1 — макро
    macro_lines = ["=== Слой 1 — МАКРО (рынок целиком) ==="]
    for sym, nm in MACRO.items():
        macro_lines.append(_fmt_tech(nm, _tech(sym)))
    macro_ctx = "\n".join(macro_lines) + "\n\n" + _fmt_sentiment()

    # Слой 2 — сектор (peers)
    peers = [p.strip().upper() for p in args.peers.split(",") if p.strip()]
    sector_lines = ["=== Слой 2 — СЕКТОР (peers) ==="]
    if peers:
        for p in peers:
            pe = resolve(p) or TICKERS_BY_KEY.get(p)
            sector_lines.append(_fmt_tech(p, _tech((pe or {}).get("yf") or p)))
    else:
        sector_lines.append("  (peers не заданы — задай --peers для секторного слоя)")
    sector_ctx = "\n".join(sector_lines)

    # Слой 3 — бумага (техника + глубина объёма)
    ticker_ctx = "=== Слой 3 — БУМАГА (техника) ===\n" + _fmt_tech(key, _tech(yf_symbol))
    vd = _fmt_vol_depth(_volume_depth(yf_symbol))
    if vd:
        ticker_ctx += "\n\n" + vd

    # Слой 3.5 — опционный сентимент (Yahoo + py_vollib), тихий фолбэк
    print(f"→ Опционный сентимент для {key}…", flush=True)
    opts_ctx = _options_ctx(yf_symbol)
    if opts_ctx:
        ticker_ctx += "\n\n" + opts_ctx
        print("  ✓ опционы получены", flush=True)
    else:
        print("  · опционов нет — работаем на технике", flush=True)

    # Слой 4 — наши сценарии (опора/рамка, не гейт)
    scen_ctx = "=== Слой 4 — НАШИ СЦЕНАРИИ (опора, не гейт) ===\n" + (
        _scenarios_ctx(cand) if cand else
        "Наших сценариев по этому тикеру нет — слой 4 пропускаем, "
        "решение строим на слоях 1–3.5.")

    # Факты и гипотезы разделены сознательно: 30.07 допущение «форс-продавец ещё
    # не закончил разгрузку» ушло в промпт под видом факта и вышло триггером
    # входа №1 по NBIS — условием, наступление которого невозможно наблюдать
    # (13F выходит с лагом в квартал). Гипотеза не имеет права быть гейтом.
    note_ctx = (f"\n\n=== ПРОВЕРЯЕМЫЕ факты от Дениса (есть источник, "
                f"учитывай наравне с данными) ===\n{args.note}") if args.note else ""
    hypo_ctx = (f"\n\n=== ГИПОТЕЗЫ (НЕ подтверждены источником) ===\n{args.hypothesis}\n"
                "Правила по гипотезам: помечай их как гипотезы в тексте; НЕ используй "
                "как гейт слоя и НЕ делай триггером входа. Триггер обязан быть "
                "наблюдаемым в данных (цена, объём, OI, публичный отчёт с датой). "
                "Если гипотеза важна — сформулируй, каким НАБЛЮДАЕМЫМ признаком её "
                "можно подтвердить или опровергнуть.") if args.hypothesis else ""
    data = (f"{macro_ctx}\n\n{sector_ctx}\n\n{ticker_ctx}\n\n{scen_ctx}"
            f"{note_ctx}{hypo_ctx}")

    # v2: аналитик — сам Claude Code (без OpenRouter). Скрипт собирает объективные
    # слои и печатает их + метод-воронку; воронку прогоняет аналитик в ответе,
    # свежие новости добирает своим web-поиском (не Perplexity).
    task = open(os.path.join(SKILL_DIR, "task_entry.md"), encoding="utf-8").read()
    bar = "=" * 72
    print(f"\n{bar}\nДАННЫЕ ДЛЯ ВОРОНКИ ENTRY-TIMING — {key}\n"
          "Аналитик (Claude Code): прогони метод-воронку по этим слоям, свежие\n"
          "новости добери web-поиском, выдай вердикт по слоям + уровни + R/R.\n"
          f"{bar}")
    print(data)
    print(f"\n{bar}\n=== МЕТОД ВОРОНКИ (task_entry.md) ===\n{bar}\n{task}")


if __name__ == "__main__":
    sys.exit(main() or 0)
