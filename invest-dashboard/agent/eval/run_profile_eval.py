#!/usr/bin/env python3
"""Офлайн-регрессия слоя 0 v2 — профиль бумаги (SPC-008, agent/profile.py).

Профиль решает, чем считается базовая линия: тип бизнеса задаёт меру,
физические драйверы, потолки роста, сопоставимые и единицу мультипликатора.
Знание о бумаге накапливается событиями в agent/agent.db и переживает прогоны:
константы по типу — seed, события перекрывают его. Смена меры — событие
особого веса: в силу вступает только подтверждение человеком конкретного
события, остальные proposals остаются отклонёнными.

Эвал гоняется на изолированной БД (AGENT_PROFILE_DB) — ни боевая invest.db,
ни рабочий журнал agent/agent.db не затрагиваются.

    python3 agent/eval/run_profile_eval.py   # exit 0, если все PASS
"""
import dataclasses
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from agent import profile, profile_store  # noqa: E402
from agent.profile import Profile  # noqa: E402

# Локация БД слоя — контракт: читаем атрибут модуля, пока переменная окружения
# ещё не выставлена и модуль указывает на agent/agent.db по умолчанию.
DEFAULT_DB = profile_store.DB_PATH

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
    """Команда в отдельном процессе: тот же журнал, тот же слой."""
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


# Тип бизнеса даёт меру, физические драйверы, потолок и сопоставимых. Развилка
# внутри research_type решается явным списком, поэтому в выборке и банк, и
# процессинг fintech-типа, и два разных ai_infra.
EXPECTED = {
    "KSPI": dict(kind="банк", measure=profile.DDM_RI, metric="pbv_per_roe",
                 research_type="fintech",
                 driver="ROE", cap="ROE сходится к стоимости капитала",
                 comp="ITUB"),
    "DELL": dict(kind="железа", measure=profile.LEVERED, metric="forward_pe",
                 research_type="ai_infra",
                 driver="backlog AI-сегмента",
                 cap="backlog не превращается в выручку быстрее цикла поставок",
                 comp="HPE"),
    "MU":   dict(kind="памяти", measure=profile.LEVERED, metric="forward_pe",
                 research_type="ai_infra",
                 driver="цена памяти", cap="исторический размах цикла",
                 comp="WDC"),
}


def section_seed(state):
    print("\n— тип бизнеса даёт меру, драйверы, потолки и сопоставимые —")
    check("чтение не создаёт журнал: его пока нет, а профиль уже собирается",
          not Path(state["db"]).exists() and profile.build("KSPI").measure)

    built = {}
    for ticker, want in EXPECTED.items():
        p = profile.build(ticker)
        built[ticker] = p
        _eq(f"{ticker}: research_type читается из tickers.py",
            p.research_type, want["research_type"])
        check(f"{ticker}: мера — {want['measure']} ({want['kind']})",
              p.measure == want["measure"] and want["kind"] in p.business_kind,
              f"мера {p.measure!r}, бизнес {p.business_kind!r}")
        check(f"{ticker}: драйверы про {want['driver']}",
              any(want["driver"] in d for d in p.drivers), f"{p.drivers!r}")
        check(f"{ticker}: потолок про «{want['cap']}»",
              any(want["cap"] in c for c in p.caps), f"{p.caps!r}")
        check(f"{ticker}: сопоставимые содержат {want['comp']}",
              want["comp"] in p.comps, f"{p.comps!r}")
        _eq(f"{ticker}: единица сравнения мультипликатора",
            p.multiple_metric, want["metric"])

    print("\n— типы различаются, а не один частный случай —")
    _eq("банк и железо считаются разными мерами",
        built["KSPI"].measure != built["DELL"].measure, True)
    _eq("железо и память считаются в разных драйверах",
        built["DELL"].drivers != built["MU"].drivers, True)
    _eq("у банка и железа нет общих сопоставимых",
        bool(set(built["KSPI"].comps) & set(built["DELL"].comps)), False)

    print("\n— явные списки решают развилку внутри research_type —")
    _eq("PYPL тоже fintech, но не банк → обычный бизнес",
        (profile.build("PYPL").measure, profile.build("PYPL").business_kind),
        (profile.LEVERED, "обычный операционный бизнес"))

    check("тикер вне tickers.py отказывает явно, а не тихо становится generic",
          _raises(lambda: profile.build("ZZZZ")))

    print("\n— форма объекта — та же, что у первоисточника —")
    _eq("поля перекрытия совпадают с полями профиля, кроме собираемых",
        tuple(profile_store.OVERRIDABLE),
        tuple(f for f in Profile.__dataclass_fields__
              if f not in ("ticker", "research_type", "core")))
    nbis = profile.build("NBIS")
    _eq("effective = core or self (у не-холдинга это сам профиль)",
        built["KSPI"].effective is built["KSPI"], True)
    _eq("сигнатура Profile несёт core",
        "core" in Profile.__dataclass_fields__, True)
    check("dataclass заморожен — профиль не мутируют на месте",
          _raises(lambda: setattr(nbis, "measure", "x"),
                  dataclasses.FrozenInstanceError))


def section_holding():
    print("\n— у холдинга профиль составной: обёртка задаёт меру, природа живёт в core —")
    p = profile.build("NBIS")
    _eq("NBIS: research_type из tickers.py", p.research_type, "ai_infra")
    _eq("NBIS: has_stakes → мера sotp (сумма частей)", p.measure, profile.SOTP)
    check("NBIS: обёртка — холдинг", "холдинг" in p.business_kind,
          f"{p.business_kind!r}")
    check("NBIS: core — аренда вычислительной мощности",
          p.core is not None and "мощности" in p.core.business_kind,
          f"core={p.core.business_kind!r}" if p.core else "core=None")
    check("NBIS: effective — это core, не обёртка", p.effective is p.core)
    _eq("NBIS: мера у обёртки, не у core", p.effective.measure, profile.EV_REVENUE)
    _eq("NBIS: effective отдаёт мегаватты, а не оценку долей",
        p.effective.drivers[0], "развёрнутые МВт")
    _eq("NBIS: effective отдаёт неоклаудные сопоставимые",
        p.effective.comps, ("IREN", "CRWV"))
    _eq("NBIS: единица сравнения — EV/Sales", p.effective.multiple_metric,
        "ev_per_sales")
    check("NBIS: пробел данных — то, чего нет в автоисточнике у core",
          "развёрнутые МВт" in p.effective.data_gaps, f"{p.effective.data_gaps!r}")
    check("NBIS: потолок — законтрактованная мощность, живёт тоже в core",
          any("законтрактованная мощность" in c for c in p.effective.caps),
          f"{p.effective.caps!r}")


def section_events(state):
    print("\n— событие перекрывает seed —")
    check("поле, не требующее подтверждения, применяется сразу",
          profile.record_change("KSPI", "comps", ["ITUB", "SBER"],
                                reason="Kaspi сравнивают с бразильским и "
                                       "индийским процессингом, а не только "
                                       "с ITUB",
                                author="research") is True)
    check("data_gaps у MU тоже без подтверждений",
          profile.record_change("MU", "data_gaps",
                                ["нормализованная прибыль", "квартальный ASP"],
                                reason="ASP раскрыт в письме акционерам",
                                author="material") is True)
    p = profile.build("KSPI")
    _eq("comps у KSPI теперь из события", p.comps, ("ITUB", "SBER"))
    _eq("мера KSPI при этом осталась из seed", p.measure, profile.DDM_RI)
    _eq("data_gaps у MU дополнены", profile.build("MU").data_gaps,
        ("нормализованная прибыль", "квартальный ASP"))

    print("\n— у холдинга событие про природу попадает в core —")
    profile.record_change("NBIS", "drivers",
                          ["развёрнутые МВт", "выручка на МВт", "загрузка",
                           "контракты на 5 ГВт"],
                          reason="NBIS раскрыл законтрактованные 5 ГВт в отчёте",
                          author="material")
    check("drivers NBIS читаются через effective",
          "контракты на 5 ГВт" in profile.build("NBIS").effective.drivers,
          f"{profile.build('NBIS').effective.drivers!r}")
    check("мера NBIS при этом не сдвинулась",
          profile.build("NBIS").measure == profile.SOTP)

    print("\n— знание переживает прогоны: лежит в файле, читается чужим процессом —")
    conn = sqlite3.connect(state["db"])
    try:
        rows = conn.execute("select count(*) from profile_events").fetchone()[0]
    finally:
        conn.close()
    check("события записаны в agent/agent.db на диске", rows >= 3, f"строк: {rows}")


def section_measure_change(state):
    print("\n— смена меры не вступает в силу без человека —")
    first = profile.record_change(
        "NBIS", "measure", profile.LEVERED,
        reason="предложение модели, от которого человек откажется",
        author="model")
    second = profile.record_change(
        "NBIS", "measure", profile.EV_REVENUE,
        reason="доля в Meta продана: долей больше нет, SOTP не о чем считать",
        author="model")
    _eq("смена меры записана неподтверждённой", (first, second), (False, False))
    _eq("в состоянии прежняя мера", profile.build("NBIS").measure, profile.SOTP)
    state_replay, pending = profile_store.replay("NBIS")
    _eq("события не попали в состояние", state_replay.get("measure"), None)
    _eq("оба предложения ждут решения", [i["field"] for i in pending],
        ["measure", "measure"])

    _eq("подтверждено ровно то, что человек подтвердил",
        profile_store.confirm("NBIS", pending[1]["id"]), 1)
    _eq("человек выбрал второе предложение — мера меняется",
        profile.build("NBIS").measure, profile.EV_REVENUE)
    _eq("отклонённое предложение в силу не вступило",
        [i["id"] for i in profile_store.replay("NBIS")[1]],
        [pending[0]["id"]])
    _eq("повторное подтверждение того же не находит ничего",
        profile_store.confirm("NBIS", pending[1]["id"]), 0)
    check("неподтверждённое событие не подтверждается задним числом другим "
          "полем", profile_store.confirm("NBIS", 10 ** 6) == 0)
    state["accepted_id"] = pending[1]["id"]
    state["rejected_id"] = pending[0]["id"]


def section_history(state):
    print("\n— история отвечает, когда и почему стало так —")
    tl = profile_store.timeline("NBIS")
    check("у каждого события автор и причина",
          all(r["author"] and r["reason"] and r["field"] for r in tl),
          f"{[(r['field'], r['author']) for r in tl]!r}")
    _eq("порядок — по записи", [r["field"] for r in tl],
        ["drivers", "measure", "measure"])
    _eq("авторы событий различаются", sorted({r["author"] for r in tl}),
        ["material", "model"])
    m_row = [r for r in tl if r["id"] == state["accepted_id"]][0]
    _eq("старое значение меры — мера из seed, а не пустота",
        m_row["old_value"], "sotp")
    _eq("подтверждение человека тоже в истории",
        (m_row["confirmed_by"], bool(m_row["confirmed_at"])), ("human", True))
    rejected = [r for r in tl if r["id"] == state["rejected_id"]][0]
    _eq("отклонённое предложение видно в истории как отклонённое",
        (rejected["confirmed"], rejected["confirmed_by"]), (0, None))
    check("история другого тикера не смешана",
          all(r["ticker"] == "NBIS" for r in tl)
          and len(profile_store.timeline("MU")) == 1,
          f"MU: {profile_store.timeline('MU')!r}")


def section_contracts(state):
    print("\n— контракты слоя —")
    _eq("у слоя своя БД в agent/, боевая не открывается",
        (DEFAULT_DB.name, DEFAULT_DB.parent.name), ("agent.db", "agent"))
    check("опечатка в имени поля отказывает явно, а не тихо ничего не меняет",
          _raises(lambda: profile_store.record("KSPI", "не_поле", "x",
                                               reason="опечатка")))
    check("пустое значение не пишется: оно уронило бы профиль при реплее",
          _raises(lambda: profile_store.record("KSPI", "comps", None,
                                               reason="снесли comps")))
    check("скаляр вместо списка не пишется",
          _raises(lambda: profile_store.record("KSPI", "comps", "ITUB",
                                               reason="одна строка")))
    check("автор вне словаря решения 3 не пишется",
          _raises(lambda: profile_store.record("KSPI", "comps", ["ITUB"],
                                               reason="чужой автор",
                                               author="нейросеть")))
    check("событие без причины не пишется",
          _raises(lambda: profile_store.record("KSPI", "comps", ["ITUB"],
                                               reason="   ")))

    print("\n— команда читает и пишет тот же журнал из отдельного процесса —")
    out = run_cli("set", "KSPI", "measure", "levered",
                  "--reason", "предложено моделью", "--author", "model")
    check("cli.py set завершается без ошибки", out.returncode == 0,
          out.stderr.strip()[-300:])
    check("команда называет смену меры предложенной",
          "предложенное" in out.stdout and "подтверждения не требует" not in out.stdout,
          out.stdout.strip()[-200:])
    ev = [r for r in profile_store.timeline("KSPI") if r["field"] == "measure"]
    _eq("в событии старое значение — мера из seed, а не пустота",
        ev[-1]["old_value"], profile.DDM_RI)

    out = run_cli("profile", "NBIS")
    check("cli.py profile NBIS завершается без ошибки", out.returncode == 0,
          out.stderr.strip()[-300:])
    check("выводит меру после подтверждения", profile.EV_REVENUE in out.stdout,
          out.stdout[:300])
    check("выводит неоклаудные сопоставимые из core",
          "IREN" in out.stdout and "CRWV" in out.stdout)
    check("выводит автора и причину смены меры",
          "Meta продана" in out.stdout and "model" in out.stdout)
    check("показывает отклонённое предложение как перекрытое",
          "перекрыто" in out.stdout, out.stdout[-400:])

    out = run_cli("profile", "KSPI")
    check("cli.py profile KSPI показывает меру банка",
          out.returncode == 0 and profile.DDM_RI in out.stdout, out.stdout[:200])
    check("не называет меру банка ждущей слой базовой линии — она уже считается",
          "ждёт слой базовой линии" not in out.stdout)

    out = run_cli("profile", "NOPE")
    check("неизвестный тикер — ошибка команды, не падение слоя",
          out.returncode == 2 and out.stderr.strip().startswith("ошибка"),
          f"returncode={out.returncode}, stderr={out.stderr.strip()[:120]}")
    out = run_cli("confirm", "KSPI", str(10 ** 6))
    check("подтверждение чужого номера — ошибка команды",
          out.returncode == 2, f"returncode={out.returncode}")


def _invest_state():
    """Состояние боевой БД: слой v2 не имеет права её менять."""
    path = ROOT / "invest.db"
    if not path.exists():
        return None
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def main() -> int:
    print("\nОфлайн-регрессия слоя 0 v2 — профиль бумаги (agent/profile.py)\n")
    before = _invest_state()
    with tempfile.TemporaryDirectory() as td:
        os.environ["AGENT_PROFILE_DB"] = str(Path(td) / "agent.db")
        state = {"db": os.environ["AGENT_PROFILE_DB"]}
        try:
            section_seed(state)
            section_holding()
            section_events(state)
            section_measure_change(state)
            section_history(state)
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
    print(f"{GREEN}Все PASS{RESET} — профиль выводится из типа бизнеса, копится "
          f"событиями и не меняет меру без человека.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
