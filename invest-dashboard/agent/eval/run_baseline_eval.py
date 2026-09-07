#!/usr/bin/env python3
"""Офлайн-регрессия слоя 1 v2 — базовая линия и когерентность (SPC-008, agent/baseline.py).

Базовая линия считается мерой, назначенной слоем профиля, а входные факты
проверяются на когерентность ДО расчёта: одна ли валюта у цены и отчётности,
сходятся ли акции с прибылью и EPS, не устарел ли период относительно
доступного. При рассогласовании — отказ с указанием, какое число запросить из
отчётности, и без единого числа в результате.

Для банка мера — триангуляция двух независимых оценок при ОДНОМ наборе
допущений (DDM и справедливый P/Bv при данном ROE): коридор даёт расхождение
мер, а не перебор допущений. Дивиденд — объявленный вперёд, не выплаченный за
прошлые 12 месяцев: на KSPI подмена 8.72 на 5.74 USD роняет DDM с 118.77 до
78.21.

Эвал гоняется на фикстурах фактов, без сети. Журнал профиля подменяется
(AGENT_PROFILE_DB) — ни боевая invest.db, ни рабочий agent/agent.db не
затрагиваются.

    python3 agent/eval/run_baseline_eval.py   # exit 0, если все PASS
"""
import inspect
import json
import os
import subprocess
import sys
from unittest.mock import patch
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agent import baseline, coherence, profile  # noqa: E402

CLI = str(ROOT / "agent" / "cli.py")
GREEN, RED, GREY, RESET = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
FAILURES = []

# Курс KZT→USD и допущения банка — из прототипа (prototype_fork/bank_methods.py,
# prototype_fork/baseline.py): ставка и рост объявлены явно, с происхождением.
FX = 0.0021820245310664177
RATE, GROWTH = 0.17, 0.09
RATE_WHY = "ставка ЦБ РК плюс премия за акционерный риск, в тенге"
GROWTH_WHY = "инфляция Казахстана плюс реальный рост экономики"

# Прежняя мера (FCF-DCF из v1) на тех же фактах — ориентир спеки SPC-008.
V1_FCF_CORRIDOR = (59.19, 67.75)
PRICE = 105.55

# KSPI, отчёт за 2К26: капитал, прибыль, EPS и объявленный дивиденд из одного
# отчёта (правило прототипа — числа берутся согласованно). Прибыль — за 12
# месяцев к дате отчёта, иначе ROE считался бы по куску года; капитал —
# квартальный, доступный в источнике, а не прошлый год: на нём меры сходятся
# до ~1%. Фикстура, не живой вызов: эвал не ходит в сеть.
KSPI = dict(
    ticker="KSPI", price=PRICE, price_currency="USD", financial_currency="KZT",
    fx=FX, shares=190_027_266,
    shares_source="190 027 266 акций в обращении, отчёт за 2К26",
    net_income=1_073_180_000_000, eps=5647.51,
    book_value=2_827_400_000_000,
    period_end="2026-06-30", available_end="2026-06-30",
    dps_declared=3994.8,
    dps_declared_source="объявлен компанией вперёд до 1кв27, отчёт за 2К26",
    dps_trailing=2630.58,
    price_context={
        "earnings_data_stale": False,
        "most_recent_earnings_date": "2026-06-30",
    },
    source="материал: отчёт Kaspi.kz за 2К26",
)

# DELL — реальный случай прототипа: автоисточник отдаёт basic shares, а EPS
# считается на diluted. Валюта одна, рассогласование внутри отчётности.
DELL = dict(
    ticker="DELL", price=120.0, price_currency="USD", financial_currency="USD",
    fx=None, shares=325_000_000, shares_source="sharesOutstanding автоисточника",
    net_income=4_576_000_000, eps=6.69, book_value=21_000_000_000,
    period_end="2026-01-31", available_end="2026-01-31",
    source="автоисточник",
)


def check(desc, cond, detail=""):
    if cond:
        print(f"{GREEN}✓ PASS{RESET}  {desc}")
    else:
        FAILURES.append(desc)
        print(f"{RED}✗ FAIL{RESET}  {desc}")
        if detail:
            print(f"         {GREY}{detail}{RESET}")


def _eq(desc, got, want):
    check(desc, got == want, f"получено {got!r}, ожидалось {want!r}")


def facts(**over):
    raw = dict(KSPI)
    raw.update(over)
    return coherence.Facts.from_dict(raw)


def run(**over):
    return baseline.build("KSPI", facts(**over), rate=RATE, growth=GROWTH,
                          rate_why=RATE_WHY, growth_why=GROWTH_WHY)


def by_code(result, code):
    found = [m for m in result.mismatches if m.code == code]
    return found[0] if found else None


def run_cli(*args):
    """Команда в отдельном процессе: тот же слой, факты из файла."""
    isolated_cli = (
        "import runpy\n"
        "from unittest.mock import patch\n"
        "with patch('agent.cli.prisms.gate', return_value=None), "
        "patch('agent.baseline.prisms.gate', return_value=None):\n"
        f"    runpy.run_path({CLI!r}, run_name='__main__')\n"
    )
    return subprocess.run([sys.executable, "-c", isolated_cli, *args],
                          capture_output=True, text=True, cwd=ROOT)


def section_baseline():
    print("\n— коридор банка — расхождение двух мер при одном наборе допущений —")
    res = run()
    check("когерентные факты дают коридор, а не отказ", not res.is_refusal(),
          "; ".join(f"{m.code}: {m.message}" for m in res.mismatches))
    _eq("коридор — две меры по возрастанию", res.corridor, (117.51, 118.77))
    check("ориентир спеки: 117.49–118.77 против 59.19–67.75 прежней мерой",
          abs(res.corridor[0] - 117.49) < 0.05
          and abs(res.corridor[1] - 118.77) < 0.01,
          f"коридор {res.corridor!r}")
    check("расхождение мер попало в результат и около 1%",
          res.divergence is not None and 0.005 < res.divergence < 0.02,
          f"{res.divergence!r}")
    _eq("меры сходятся — оценка устойчива", res.converged, True)
    _eq("порог сходимости объявлен, а не спрятан в сравнении",
        baseline.CONVERGENCE_TOLERANCE, 0.05)
    check("коридор выше верха прежней меры — вывод инвертируется",
          res.corridor[0] > V1_FCF_CORRIDOR[1],
          f"{res.corridor[0]} против верха v1 {V1_FCF_CORRIDOR[1]}")
    check("вердикт «дешевле» там, где прежняя мера давала «дорого на +56%»",
          res.verdict and "дешевле" in res.verdict and "11%" in res.verdict,
          f"{res.verdict!r}")

    print("\n— мера берётся из профиля, слой её не выбирает —")
    p = profile.build("KSPI")
    _eq("мера в результате — мера профиля", res.measure, p.measure)
    _eq("основание меры — из профиля", res.measure_reason, p.measure_reason)

    print("\n— допущения объявлены явно, с происхождением —")
    d = res.detail
    _eq("обе меры названы", d.measures, ("ddm", "pbv"))
    check("один набор допущений на обе меры", (d.r, d.g) == (RATE, GROWTH),
          f"{d!r}")
    check("ставка объявлена с происхождением", "ЦБ РК" in d.r_why, d.r_why)
    check("рост объявлен с происхождением", "инфляц" in d.g_why, d.g_why)
    check("ROE посчитан из прибыли и капитала одного отчёта",
          abs(d.roe - KSPI["net_income"] / KSPI["book_value"]) < 1e-4, d.roe)
    check("справедливый P/Bv × капитал на акцию даёт вторую меру",
          abs(d.pbv_multiple * d.book_value_per_share - d.pbv) < 0.05,
          f"{d.pbv_multiple} × {d.book_value_per_share} = "
          f"{d.pbv_multiple * d.book_value_per_share:.2f}")
    check("курс объявлен: конвертацию делает код (D20)",
          d.fx == FX and d.financial_currency == "KZT"
          and d.price_currency == "USD")
    return res


def section_declared(res):
    print("\n— дивиденд объявленный вперёд, не выплаченный за прошлые 12 месяцев —")
    _eq("в расчёте объявленный дивиденд (8.72 USD)",
        res.detail.dps, round(KSPI["dps_declared"] * FX, 2))
    _eq("выплаченный за 12 месяцев показан рядом, но не в расчёте",
        res.detail.dps_trailing, round(KSPI["dps_trailing"] * FX, 2))
    check("происхождение дивиденда объявлено", "2К26" in res.detail.dps_why,
          res.detail.dps_why)
    would_be = round(res.detail.dps_trailing * (1 + GROWTH) / (RATE - GROWTH), 2)
    _eq("подмена объявленного выплаченным роняет DDM до 78.21", would_be, 78.21)
    check("потеря — 40.56, треть оценки, при том же наборе допущений",
          abs((res.detail.ddm - would_be) - 40.56) < 0.01,
          f"{res.detail.ddm} − {would_be} = {res.detail.ddm - would_be:.2f}")

    print("\n— альтернативы допущений не перебираются: коридор — не перебор —")
    check("в деталях одна ставка и один рост, а не сетка",
          [a for a in ("r_low", "r_high", "g_low", "g_high")
           if hasattr(res.detail, a)] == [])


def section_refusals():
    print("\n— отказ до числа: две валюты без объявленного курса —")
    res = run(fx=None)
    check("расчёт остановлен, коридора нет", res.is_refusal() and res.corridor is None,
          f"{res.corridor!r}")
    m = by_code(res, "currency")
    check("отказ называет обе валюты",
          m and "USD" in m.message and "KZT" in m.message,
          m.message if m else "нет отказа про валюту")
    check("отказ говорит, какое число запросить", m and "курс" in m.request,
          m.request if m else "—")
    check("чисел в отказе нет: и коридора, и деталей", res.detail is None)

    print("\n— отказ до числа: курс единица при разных валютах — тот же расчёт через две —")
    res = run(fx=1)
    m = by_code(res, "currency")
    check("конвертации нет — расчёт не начинается", res.is_refusal() and m,
          f"{res.corridor!r}")
    check("отказ называет курс, который не конвертирует",
          m and "1" in m.message, m.message if m else "—")

    print("\n— отказ до числа: акции, прибыль и EPS не сходятся —")
    res = run(eps=3.30)  # EPS в валюте котировки против прибыли в валюте отчётности
    m = by_code(res, "income_eps")
    check("рассогласование EPS с прибылью — отказ", res.is_refusal() and m)
    check("отказ показывает рассогласование числами",
          m and "3.3" in m.message and "1 073" in m.message,
          m.message if m else "—")
    check("отказ требует прибыль и EPS из одного отчёта за один период",
          m and "EPS" in m.request and "отчёт" in m.request, m.request if m else "—")

    print("\n— отказ до числа: период устарел относительно доступного —")
    res = run(period_end="2025-12-31", available_end="2026-06-30")
    m = by_code(res, "period")
    check("устаревшая отчётность — отказ, а не расчёт по прошлому году",
          res.is_refusal() and m, f"{res.corridor!r}")
    check("отказ называет оба периода",
          m and "2025-12-31" in m.message and "2026-06-30" in m.message,
          m.message if m else "—")
    check("отказ просит баланс и прибыль за доступный период",
          m and "2026-06-30" in m.request, m.request if m else "—")

    print("\n— отказ до числа: объявленного дивиденда нет —")
    res = run(dps_declared=None)
    m = by_code(res, "dps_declared")
    check("нет объявленного дивиденда — отказ, а не подмена выплаченным",
          res.is_refusal() and m, f"{res.corridor!r}")
    check("код отказа называет поле, которого не хватило",
          m and m.code == "dps_declared")
    check("отказ объясняет, почему выплаченный не замена",
          m and "5.74" in m.request and "8.72" in m.request,
          m.request if m else "—")

    print("\n— одно рассогласование не прячет остальные —")
    res = run(fx=None, eps=3.30, period_end="2025-12-31", available_end="2026-06-30")
    codes = {m.code for m in res.mismatches}
    check("все три рассогласования в одном отказе",
          {"currency", "income_eps", "period"} <= codes, f"{codes!r}")
    check("каждое со своим числом и своей просьбой",
          all(m.message and m.request for m in res.mismatches))


def section_measure():
    print("\n— мера, назначенная профилем, но не перенесённая в слой, отказывает —")
    print(f"{GREY}  (перенос levered / ev_revenue / sotp из v1 — отдельный тикет "
          f"среза; здесь важно, что слой не считает их молча другой формулой){RESET}")
    res = baseline.build("DELL", coherence.Facts.from_dict(DELL), rate=0.11,
                         growth=0.03, rate_why="ставка USD", growth_why="ВВП США")
    check("levered слоем базовой линии не считается", res.is_refusal(),
          f"коридор {res.corridor!r}")
    m = by_code(res, "measure")
    check("отказ называет меру и то, что она из профиля",
          m and "levered" in m.message, m.message if m else "—")
    check("когерентность при этом проверена: у DELL акции и EPS не сходятся",
          by_code(res, "income_eps") is not None,
          "; ".join(x.code for x in res.mismatches))


def section_contract():
    print("\n— контракты слоя —")
    for mod in (baseline, coherence):
        src = inspect.getsource(mod)
        check(f"{mod.__name__.split('.')[-1]}.py не ходит в сеть",
              not any(w in src for w in ("yfinance", "requests", "urllib", "http")))

    print("\n— команда читает факты из файла и печатает отказ решением —")
    with tempfile.TemporaryDirectory() as td:
        good = Path(td) / "kspi.json"
        good.write_text(json.dumps(KSPI, ensure_ascii=False), encoding="utf-8")
        assumptions = ("--rate", str(RATE), "--growth", str(GROWTH),
                       "--rate-why", RATE_WHY, "--growth-why", GROWTH_WHY)
        args = ("baseline", "KSPI", "--facts", str(good), *assumptions)
        out = run_cli(*args)
        check("команда считает коридор", out.returncode == 0, out.stderr.strip()[-300:])
        check("в выводе обе границы и расхождение мер",
              "117.51" in out.stdout and "118.77" in out.stdout
              and "1.1" in out.stdout, out.stdout[:400])
        check("в выводе вердикт против цены", "дешевле" in out.stdout)
        check("в выводе происхождение ставки, роста и дивиденда",
              "ЦБ РК" in out.stdout and "инфляц" in out.stdout
              and "объявлен" in out.stdout, out.stdout[:600])

        bad = dict(KSPI)
        bad["fx"] = None
        refused = Path(td) / "kspi_no_fx.json"
        refused.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        out = run_cli("baseline", "KSPI", "--facts", str(refused), *assumptions)
        check("отказ команды — код 3, а не падение", out.returncode == 3,
              f"returncode={out.returncode}, stderr={out.stderr.strip()[-200:]}")
        check("команда печатает, какое число запросить", "курс" in out.stdout,
              out.stdout[:300])

        typo = dict(KSPI)
        typo["book_valye"] = typo.pop("book_value")
        typo_path = Path(td) / "typo.json"
        typo_path.write_text(json.dumps(typo, ensure_ascii=False), encoding="utf-8")
        out = run_cli("baseline", "KSPI", "--facts", str(typo_path), *assumptions)
        check("опечатка в имени факта — ошибка команды, а не тихий пропуск",
              out.returncode == 2 and "book_valye" in out.stderr,
              f"returncode={out.returncode}, stderr={out.stderr.strip()[-200:]}")

        out = run_cli("baseline", "KSPI", "--facts", str(good))
        check("без объявленной ставки команда не считает", out.returncode == 2,
              f"returncode={out.returncode}")

        out = run_cli("baseline", "NOPE", "--facts", str(good), *assumptions)
        check("нет в tickers.py — ошибка команды", out.returncode == 2,
              f"returncode={out.returncode}")

        golden = json.loads(
            (ROOT / "agent" / "eval" / "fixtures" / "golden_cases.json")
            .read_text(encoding="utf-8"))
        cases = {case["ticker"]: case for case in golden["cases"]}
        for ticker, measure in (("DELL", "levered"), ("NBIS", "sotp")):
            case = cases[ticker]["baseline"]
            path = Path(td) / f"{ticker.lower()}_assumptions.json"
            path.write_text(json.dumps(case["assumptions"]), encoding="utf-8")
            fresh_facts = Path(td) / f"{ticker.lower()}_facts.json"
            fresh_facts.write_text(json.dumps({"price_context": {
                "earnings_data_stale": False,
                "most_recent_earnings_date": "2026-06-30",
            }}), encoding="utf-8")
            out = run_cli("baseline", ticker, "--assumptions", str(path),
                          "--facts", str(fresh_facts))
            low, high = case["expected_corridor"]
            check(f"команда считает {measure} из --assumptions",
                  out.returncode == 0,
                  f"returncode={out.returncode}, stderr={out.stderr.strip()[-300:]}")
            check(f"вывод {measure} содержит меру и обе границы коридора",
                  measure in out.stdout and f"{low:.2f}" in out.stdout
                  and f"{high:.2f}" in out.stdout, out.stdout[:400])


def _invest_state():
    """Состояние боевой БД: слой v2 не имеет права её менять."""
    path = ROOT / "invest.db"
    if not path.exists():
        return None
    st = path.stat()
    return st.st_size, st.st_mtime_ns


@patch("agent.baseline.prisms.gate", return_value=None)
def main(_prisms_gate) -> int:
    print("\nОфлайн-регрессия слоя 1 v2 — базовая линия и когерентность "
          "(agent/baseline.py)\n")
    before = _invest_state()
    with tempfile.TemporaryDirectory() as td:
        os.environ["AGENT_PROFILE_DB"] = str(Path(td) / "agent.db")
        try:
            res = section_baseline()
            section_declared(res)
            section_refusals()
            section_measure()
            section_contract()
        finally:
            os.environ.pop("AGENT_PROFILE_DB", None)
    check("боевая invest.db не изменилась ни байтом", _invest_state() == before,
          f"было {before}, стало {_invest_state()}")

    print()
    if FAILURES:
        print(f"{RED}{len(FAILURES)} FAIL{RESET}")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"{GREEN}Все PASS{RESET} — базовая линия считается мерой профиля, "
          f"а рассогласованные факты останавливают расчёт до числа.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
