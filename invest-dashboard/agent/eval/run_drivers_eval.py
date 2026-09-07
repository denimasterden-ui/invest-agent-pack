#!/usr/bin/env python3
"""Офлайн-регрессия ручного ввода драйверов (SPC-008, тикет 7/11).

Драйверы, которых нет в автоисточнике (data_gaps профиля), вводятся вручную
событием профиля с классом якоря, признаком подтверждения и источником — тем же
механизмом, что и всё остальное знание. Введённый драйвер участвует в расчёте
базовой линии и в проверке физических потолков, переживает перезапуск и виден
в профиле с указанием происхождения числа.

Эвал гоняется на изолированной БД (AGENT_PROFILE_DB) — ни боевая invest.db,
ни рабочий agent/agent.db не затрагиваются.

    python3 agent/eval/run_drivers_eval.py   # exit 0, если все PASS
"""
import json
import os
import sqlite3
import subprocess
import sys
from unittest.mock import patch
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agent import baseline, profile, profile_store  # noqa: E402

CLI = str(ROOT / "agent" / "cli.py")
GREEN, RED, GREY, RESET = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
FAILURES = []


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


def run_cli(*args):
    return subprocess.run([sys.executable, CLI, *args], capture_output=True,
                          text=True, cwd=ROOT)


def _raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def _invest_state():
    path = ROOT / "invest.db"
    if not path.exists():
        return None
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def section_write_and_read(state):
    print("\n— введённый драйвер записывается событием с источником —")

    # DELL: enter backlog value manually
    out = run_cli("driver", "DELL", "backlog", "15000000000",
                  "--anchor-class", "company_guide",
                  "--source", "отчёт DELL за 2К26",
                  "--confirms", "number",
                  "--reason", "компания раскрыла backlog AI-сегмента")
    check("cli.py driver DELL backlog завершается без ошибки",
          out.returncode == 0,
          f"returncode={out.returncode}, stderr={out.stderr.strip()[:300]}")
    check("вывод подтверждает запись драйвера",
          "backlog" in out.stdout and "записан" in out.stdout,
          out.stdout.strip()[:300])

    # NBIS: enter contracted_gw
    out = run_cli("driver", "NBIS", "contracted_gw", "5",
                  "--anchor-class", "company_guide",
                  "--source", "отчёт NBIS за 2К26",
                  "--confirms", "number",
                  "--reason", "компания раскрыла законтрактованные ГВт")
    check("cli.py driver NBIS contracted_gw завершается без ошибки",
          out.returncode == 0,
          f"returncode={out.returncode}, stderr={out.stderr.strip()[:300]}")

    # MU: enter normalized_earnings
    out = run_cli("driver", "MU", "normalized_earnings", "5000000000",
                  "--anchor-class", "company_guide",
                  "--source", "отчёт MU за 4К26",
                  "--confirms", "number",
                  "--reason", "компания назвала нормализованную прибыль по циклу")
    check("cli.py driver MU normalized_earnings завершается без ошибки",
          out.returncode == 0,
          f"returncode={out.returncode}, stderr={out.stderr.strip()[:300]}")

    # Verify driver facts are collected by profile
    p = profile.build("DELL")
    df = p.driver_facts
    check("DELL: driver_facts содержит backlog",
          "backlog" in df,
          f"driver_facts={df!r}")
    check("DELL: backlog value = 15B",
          df.get("backlog", {}).get("value") == 15_000_000_000,
          f"value={df.get('backlog', {}).get('value')!r}")
    check("DELL: backlog anchor_class = company_guide",
          df.get("backlog", {}).get("anchor_class") == "company_guide",
          f"anchor_class={df.get('backlog', {}).get('anchor_class')!r}")
    check("DELL: backlog source указывает на отчёт",
          "отчёт DELL" in df.get("backlog", {}).get("source", ""),
          f"source={df.get('backlog', {}).get('source')!r}")
    check("DELL: backlog confirms = number",
          df.get("backlog", {}).get("confirms") == "number",
          f"confirms={df.get('backlog', {}).get('confirms')!r}")

    p_nbis = profile.build("NBIS")
    check("NBIS: driver_facts содержит contracted_gw",
          "contracted_gw" in p_nbis.driver_facts,
          f"{p_nbis.driver_facts!r}")

    p_mu = profile.build("MU")
    check("MU: driver_facts содержит normalized_earnings",
          "normalized_earnings" in p_mu.driver_facts,
          f"{p_mu.driver_facts!r}")


def section_participates_in_calculation(state):
    print("\n— введённый драйвер участвует в расчёте базовой линии —")

    # DELL: driver_facts from profile flow into physical_caps._backlog automatically.
    # The profile already has "backlog" = 15B from the CLI driver command above.
    # We also need delivery_cycle_years and segment_revenue_next_year for the check
    # to fire — those are passed explicitly via physical.
    assumptions = {
        "fcf_base": 100, "shares": 10,
        "scenarios": {"base": {"growth_path": [0.1] * 5,
                               "discount_rate": 0.10,
                               "terminal_growth": 0.02}},
    }
    # Without additional physical data, backlog check won't fire (needs more inputs)
    result_no_driver = baseline.build("DELL", assumptions=assumptions)
    check("DELL без доп. физических данных: backlog-проверка не срабатывает "
          "(нужны ещё delivery_cycle_years и segment_revenue_next_year)",
          not any(w.code == "backlog_delivery" for w in result_no_driver.warnings),
          f"warnings={result_no_driver.warnings!r}")

    # With additional physical data + profile driver_facts (backlog=15B), the check runs.
    # The backlog value comes from the profile automatically — no need to pass it again.
    result_with_driver = baseline.build("DELL", assumptions=assumptions, physical={
        "delivery_cycle_years": 2,
        "segment_revenue_next_year": 10_000_000_000,
    })
    check("DELL: backlog из профиля + доп. данные → backlog-проверка срабатывает",
          any(w.code == "backlog_delivery" for w in result_with_driver.warnings),
          f"warnings={result_with_driver.warnings!r}")
    # Verify the backlog value in the warning matches the driver fact
    warning = next((w for w in result_with_driver.warnings
                    if w.code == "backlog_delivery"), None)
    check("DELL: в предупреждении фигурирует backlog 15B из профиля",
          warning is not None and "15,000,000,000" in warning.message,
          f"warning message={warning.message if warning else 'N/A'}")

    # NBIS: capacity check with profile driver facts + explicit physical data
    core = {"revenue_base": 3e9, "shares": 500e6, "net_cash": 1e9,
            "scenarios": {"base": {"growth_path": [1, .6, .35, .22, .15],
                           "discount_rate": .10,
                           "terminal_fcf_margin": .25,
                           "terminal_fcf_multiple": 20}}}
    result_nbis = baseline.build("NBIS", assumptions={
        "fcf_base": 1, "core": core,
        "stakes": [{"name": "ClickHouse", "ownership_pct": .28,
                     "entity_valuation": {"base": 20e9}}],
    }, physical={"revenue_per_mw": 3e6, "build_rate_gw": 1})
    # contracted_gw=5 was entered via CLI driver above — it should flow in automatically
    check("NBIS: contracted_gw из профиля + доп. данные → проверка мощности срабатывает",
          any(w.code == "capacity_total" for w in result_nbis.warnings),
          f"warnings={result_nbis.warnings!r}")

    # MU: cycle check with driver facts from profile
    result_mu = baseline.build("MU", assumptions={
        "fcf_base": 100, "shares": 10, "base_year": "FY2026 peak",
        "scenarios": {"base": {"growth_path": [0.05] * 5,
                               "discount_rate": 0.10,
                               "terminal_growth": 0.02}},
    }, physical={"cycle_position": "peak"})
    check("MU: проверка пика цикла срабатывает",
          any(w.code == "cycle_peak_base" for w in result_mu.warnings),
          f"warnings={result_mu.warnings!r}")

    # Verify that profile driver_facts don't override explicit physical values
    result_override = baseline.build("DELL", assumptions=assumptions, physical={
        "backlog": 5_000_000_000,  # explicit override
        "delivery_cycle_years": 2,
        "segment_revenue_next_year": 10_000_000_000,
    })
    warning_ov = next((w for w in result_override.warnings
                       if w.code == "backlog_delivery"), None)
    check("DELL: явный physical.backlog перекрывает профиль (5B, не 15B)",
          warning_ov is not None and "5,000,000,000" in warning_ov.message,
          f"warning message={warning_ov.message if warning_ov else 'N/A'}")


def section_survives_restart(state):
    print("\n— введённый драйвер переживает перезапуск —")

    # Verify data is on disk
    conn = sqlite3.connect(state["db"])
    try:
        rows = conn.execute(
            "select count(*) from profile_events where field='driver_facts'"
        ).fetchone()[0]
    finally:
        conn.close()
    check("driver_fact события записаны в БД на диске",
          rows >= 3, f"driver_fact строк: {rows}")

    # Re-read profile — should still have driver facts
    p = profile.build("DELL")
    check("после перечитывания профиля: backlog всё ещё есть",
          "backlog" in p.driver_facts,
          f"driver_facts={p.driver_facts!r}")
    check("после перечитывания: значение сохранилось",
          p.driver_facts.get("backlog", {}).get("value") == 15_000_000_000)

    # Second driver for same ticker merges in
    run_cli("driver", "DELL", "delivery_cycle_years", "2",
            "--anchor-class", "company_guide",
            "--source", "отчёт DELL за 2К26",
            "--confirms", "number",
            "--reason", "цикл поставки из отчёта")
    p2 = profile.build("DELL")
    check("второй драйвер того же тикера: оба драйвера в driver_facts",
          "backlog" in p2.driver_facts and "delivery_cycle_years" in p2.driver_facts,
          f"driver_facts={p2.driver_facts!r}")
    check("второй драйвер: значение delivery_cycle_years = 2",
          p2.driver_facts.get("delivery_cycle_years", {}).get("value") == 2)


def section_profile_shows_gaps(state):
    print("\n— профиль показывает, какие пробелы закрыты, а какие остались —")

    p = profile.build("DELL")
    # backlog was in data_gaps for hardware_ai
    check("DELL: data_gaps больше не содержит 'backlog' (закрыто ручным вводом)",
          "backlog" not in p.data_gaps,
          f"data_gaps={p.data_gaps!r}")
    # But "маржа сегмента" is still a gap
    check("DELL: 'маржа сегмента' всё ещё в data_gaps (не введена)",
          "маржа сегмента" in p.data_gaps,
          f"data_gaps={p.data_gaps!r}")

    p_nbis = profile.build("NBIS")
    check("NBIS: 'развёрнутые МВт' всё ещё в data_gaps (contracted_gw != развёрнутые МВт)",
          "развёрнутые МВт" in p_nbis.effective.data_gaps,
          f"data_gaps={p_nbis.effective.data_gaps!r}")


def section_origin_visible(state):
    print("\n— происхождение числа видно в выводе —")

    out = run_cli("profile", "DELL")
    check("cli.py profile DELL завершается без ошибки",
          out.returncode == 0,
          f"returncode={out.returncode}, stderr={out.stderr.strip()[:300]}")
    check("вывод профиля показывает backlog с источником",
          "backlog" in out.stdout and "отчёт DELL" in out.stdout,
          out.stdout[:500])
    check("вывод профиля показывает anchor_class",
          "company_guide" in out.stdout,
          out.stdout[:500])

    out = run_cli("profile", "MU")
    check("cli.py profile MU: источник драйвера виден",
          "normalized_earnings" in out.stdout and "отчёт MU" in out.stdout,
          out.stdout[:500])


def section_contracts(state):
    print("\n— контракты ввода драйверов —")

    # driver_facts is in OVERRIDABLE
    check("driver_facts в списке перекрываемых полей",
          "driver_facts" in profile_store.OVERRIDABLE,
          f"OVERRIDABLE={profile_store.OVERRIDABLE!r}")

    # Value must be a dict
    check("driver_facts с не-словарём отказывает",
          _raises(lambda: profile_store.record(
              "DELL", "driver_facts", "not a dict",
              reason="bad value")),
          "должно было упасть с ValueError")

    # driver_facts dict must have required keys
    check("driver_facts без поля 'value' отказывает",
          _raises(lambda: profile_store.record(
              "DELL", "driver_facts", {"anchor_class": "company_guide"},
              reason="missing value")),
          "должно было упасть с ValueError")

    # CLI rejects missing required args
    out = run_cli("driver", "DELL", "backlog", "100")
    check("cli.py driver без --anchor-class — ошибка",
          out.returncode == 2,
          f"returncode={out.returncode}, stderr={out.stderr.strip()[:200]}")

    # CLI rejects unknown anchor_class
    out = run_cli("driver", "DELL", "backlog", "100",
                  "--anchor-class", "my_opinion",
                  "--source", "я", "--confirms", "number",
                  "--reason", "test")
    check("cli.py driver с неизвестным anchor_class — ошибка",
          out.returncode == 2,
          f"returncode={out.returncode}, stderr={out.stderr.strip()[:200]}")

    # CLI rejects unknown confirms
    out = run_cli("driver", "DELL", "backlog", "100",
                  "--anchor-class", "company_guide",
                  "--source", "отчёт", "--confirms", "maybe",
                  "--reason", "test")
    check("cli.py driver с неизвестным confirms — ошибка",
          out.returncode == 2,
          f"returncode={out.returncode}, stderr={out.stderr.strip()[:200]}")

    # analyst_judgement + number is rejected (same as fork contract)
    out = run_cli("driver", "DELL", "backlog", "100",
                  "--anchor-class", "analyst_judgement",
                  "--source", "я", "--confirms", "number",
                  "--reason", "test")
    check("cli.py driver: analyst_judgement + number — ошибка",
          out.returncode == 2,
          f"returncode={out.returncode}, stderr={out.stderr.strip()[:200]}")

    # analyst_judgement + estimate is OK
    out = run_cli("driver", "DELL", "маржа сегмента", "0.25",
                  "--anchor-class", "analyst_judgement",
                  "--confirms", "estimate",
                  "--reason", "оценка аналитика")
    check("cli.py driver: analyst_judgement + estimate — ок",
          out.returncode == 0,
          f"returncode={out.returncode}, stderr={out.stderr.strip()[:200]}")


@patch("agent.baseline.prisms.gate", return_value=None)
def main(_prisms_gate) -> int:
    print("\nОфлайн-регрессия ручного ввода драйверов (agent/profile_store + agent/cli)\n")
    before = _invest_state()
    with tempfile.TemporaryDirectory() as td:
        os.environ["AGENT_PROFILE_DB"] = str(Path(td) / "agent.db")
        state = {"db": os.environ["AGENT_PROFILE_DB"]}
        try:
            section_write_and_read(state)
            section_participates_in_calculation(state)
            section_survives_restart(state)
            section_profile_shows_gaps(state)
            section_origin_visible(state)
            section_contracts(state)
        finally:
            os.environ.pop("AGENT_PROFILE_DB", None)
    check("боевая invest.db не изменилась ни байтом",
          _invest_state() == before,
          f"было {before}, стало {_invest_state()}")

    print()
    if FAILURES:
        print(f"{RED}{len(FAILURES)} FAIL{RESET}")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print(f"{GREEN}Все PASS{RESET} — драйверы вводятся с источником, "
          f"участвуют в расчёте и переживают перезапуск.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
