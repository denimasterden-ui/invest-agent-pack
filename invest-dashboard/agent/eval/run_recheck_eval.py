#!/usr/bin/env python3
"""Офлайн-регрессия слоя 4 v2 — речек: сверка условий форков (SPC-008, agent/recheck.py).

Речек сверяет условия активных форков с новыми фактами. Четыре статуса:
сработало, не сработало, пока не проверить, ПЕРЕНЕСЕНО. Перенос — не поломка
тезиса: коридор остаётся прежним, горизонт растёт, годовая доходность падает.

Несработавшее условие помечает форк требующим решения. Решение принимает
человек, автоматических действий нет.

Результат сверки пишется в память и виден в истории: эволюция тезиса должна
читаться так же, как эволюция допущений.

Эвал гоняется на замороженных фактах, без сети. Журнал профиля и речека
подменяется (AGENT_PROFILE_DB), боевая invest.db не открывается.

    python3 agent/eval/run_recheck_eval.py   # exit 0, если все PASS
"""
import json
import os
import subprocess
import sys
from unittest.mock import patch
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import baseline, coherence, fork, profile_store, recheck  # noqa: E402

CLI = str(ROOT / "agent" / "cli.py")
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GREEN, RED, GREY, RESET = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
FAILURES = []

# Те же допущения и факты, что в fork eval — базовая линия KSPI 117.51–118.77
# при цене 105.55.
FX = 0.0021820245310664177
RATE, GROWTH = 0.17, 0.09
RATE_WHY = "ставка ЦБ РК плюс премия за акционерный риск, в тенге"
GROWTH_WHY = "инфляция Казахстана плюс реальный рост экономики"

KSPI = dict(
    ticker="KSPI", price=105.55, price_currency="USD", financial_currency="KZT",
    fx=FX, shares=190_027_266,
    shares_source="190 027 266 акций в обращении, отчёт за 2К26",
    net_income=1_073_180_000_000, eps=5647.51,
    book_value=2_827_400_000_000,
    period_end="2026-06-30", available_end="2026-06-30",
    dps_declared=3994.8,
    dps_declared_source="объявлен компанией вперёд до 1кв27, отчёт за 2К26",
    dps_trailing=2630.58,
    price_context={"earnings_data_stale": False,
                   "most_recent_earnings_date": "2026-06-30"},
    source="материал: отчёт Kaspi.kz за 2К26",
)
KSPI_BASE_CORRIDOR = (117.51, 118.77)


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


def kspi_facts(**over):
    raw = dict(KSPI)
    raw.update(over)
    return coherence.Facts.from_dict(raw)


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def run_cli(*args):
    isolated_cli = (
        "import runpy\n"
        "from unittest.mock import patch\n"
        "with patch('agent.cli.prisms.gate', return_value=None), "
        "patch('agent.baseline.prisms.gate', return_value=None):\n"
        f"    runpy.run_path({CLI!r}, run_name='__main__')\n"
    )
    return subprocess.run([sys.executable, "-c", isolated_cli, *args],
                          capture_output=True, text=True, cwd=ROOT)


def section_statuses(kspi_basis):
    """Все четыре статуса воспроизводятся на замороженных фактах."""
    print("\n— все четыре статуса воспроизводятся —")
    kspi_forks, _ = fork.parse(fixture("fork_kspi_answer.json"))
    bull = next(x for x in kspi_forks if x.label == "bull")
    bear = next(x for x in kspi_forks if x.label == "bear")

    # Каждый статус на своём условии.
    bull_checks = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.TRIGGERED,
            fact="ЦБ РК снизил базовую ставку до 14.5% в ноябре 2026"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.POSTPONED,
            fact="запуск Hepsi Bank перенесён на 2028 — объявлено в отчёте за 3К26"),
    ]
    outcome = recheck.check(bull, kspi_basis, bull_checks)
    check("сработало + перенесено — не отказ", not outcome.is_refusal(),
          "; ".join(outcome.refusals) if outcome.refusals else "")
    _eq("статусы: сработало и перенесено",
        tuple(c.status for c in outcome.conditions),
        (recheck.TRIGGERED, recheck.POSTPONED))
    check("нет требования решения — нет несработавших",
          not outcome.requires_decision)
    check("перенос создал отложенный форк", outcome.deferred is not None,
          f"{outcome.deferred!r}")

    bear_checks = [
        recheck.ConditionCheck(
            condition=bear.must_be_true[0],
            status=recheck.NOT_TRIGGERED,
            fact="прибыль Kaspi.kz за 3К26 выросла на 18% г/г, а не упала"),
        recheck.ConditionCheck(
            condition=bear.must_be_true[1],
            status=recheck.CANT_CHECK,
            fact="отчёт за 3К26 не раскрывает динамику NIM — ждём годовой"),
    ]
    outcome = recheck.check(bear, kspi_basis, bear_checks)
    check("не сработало + пока не проверить — не отказ",
          not outcome.is_refusal(),
          "; ".join(outcome.refusals) if outcome.refusals else "")
    _eq("статусы: не сработало и пока не проверить",
        tuple(c.status for c in outcome.conditions),
        (recheck.NOT_TRIGGERED, recheck.CANT_CHECK))
    check("требование решения: условие не сработало",
          outcome.requires_decision)
    check("переноса нет — нет postponed",
          outcome.deferred is None, f"{outcome.deferred!r}")


def section_generation(kspi_basis):
    """SPC-009 §2.4: recheck из ответа генератора сверяется на момент генерации."""
    print("\n— recheck на момент генерации (agent.recheck.at_generation) —")
    kspi_forks, _ = fork.parse(fixture("fork_kspi_answer.json"))
    bull = next(x for x in kspi_forks if x.label == "bull")
    bear = next(x for x in kspi_forks if x.label == "bear")

    answer = "```json\n" + json.dumps({
        "forks": [
            {"label": "bull", "recheck": [
                {"condition": bull.must_be_true[0], "status": recheck.TRIGGERED,
                 "fact": "ставка по вкладам упала до 13.8% в отчёте за 4кв26"},
                {"condition": bull.must_be_true[1], "status": recheck.CANT_CHECK,
                 "fact": "отчёт за 1кв27 ещё не вышел"}]},
            {"label": "bear", "recheck": [
                {"condition": bear.must_be_true[0], "status": recheck.NOT_TRIGGERED,
                 "fact": "прибыль за 4кв26 выросла на 18% г/г, а не упала"},
                {"condition": bear.must_be_true[1], "status": recheck.CANT_CHECK,
                 "fact": "NIM за 4кв26 отчёт не раскрывает"}]}],
    }, ensure_ascii=False) + "\n```"

    outcomes, problems = recheck.at_generation(answer, (bull, bear), kspi_basis)
    check("оба форка сверены без ошибок разбора", not problems, problems)
    by_label = {o.fork_label: o for o in outcomes}
    check("bull: сработало + пока не проверить",
          tuple(c.status for c in by_label["bull"].conditions)
          == (recheck.TRIGGERED, recheck.CANT_CHECK),
          f"{by_label['bull'].conditions!r}")
    check("bear: условие отстаёт от факта помечено «не сработало»",
          by_label["bear"].conditions[0].status == recheck.NOT_TRIGGERED,
          f"{by_label['bear'].conditions!r}")

    print("\n— искажённый/отсутствующий recheck не роняет расчёт формулой отказа —")
    outcomes, problems = recheck.at_generation("не JSON вовсе", (bull, bear), kspi_basis)
    check("сломанный ответ — явная проблема, не молчаливая пустота",
          not outcomes and problems, f"{outcomes!r} {problems!r}")

    missing = "```json\n" + json.dumps({"forks": [
        {"label": "bull"}, {"label": "bear"}]}) + "\n```"
    outcomes, problems = recheck.at_generation(missing, (bull, bear), kspi_basis)
    check("форк без объявленного recheck отмечен проблемой, не пропущен молча",
          not outcomes and len(problems) == 2
          and all("recheck не объявлен" in p for p in problems), problems)

    print("\n— cmd_fork печатает generation-recheck рядом с форком (SPC-009) —")
    with tempfile.TemporaryDirectory() as td:
        facts_f = Path(td) / "kspi.json"
        facts_f.write_text(json.dumps(KSPI), encoding="utf-8")
        ans_f = Path(td) / "fork.json"
        ans_f.write_text(json.dumps({"forks": [{
            "label": "bull", "thesis": "разворот ставок поднимает прибыль",
            "channel": "прибыль", "channel_reason": "меняет forward EPS",
            "horizon_months": 12,
            "must_be_true": ["ставка по вкладам ниже 14% в 1кв27"],
            "overrides": {
                "eps_forward": {"value": 6300, "rationale": "фондирование",
                                "anchor_class": "company_guide",
                                "confirms": "driver", "source": "гайд Kaspi"},
                "pe_low": {"value": 8.0, "rationale": "нижний P/E",
                           "anchor_class": "own_history", "confirms": "number",
                           "source": "график"},
                "pe_high": {"value": 10.0, "rationale": "верхний P/E",
                            "anchor_class": "analyst_judgement",
                            "confirms": "estimate", "source": "оценка"}},
            "recheck": [{"condition": "ставка по вкладам ниже 14% в 1кв27",
                         "status": recheck.NOT_TRIGGERED,
                         "fact": "депозит 3м всё ещё 19% в августе 2026"}]}]}),
            encoding="utf-8")
        out = run_cli("fork", "KSPI", "--facts", str(facts_f), "--rate", "0.17",
                      "--growth", "0.09", "--rate-why", "r", "--growth-why", "g",
                      "--answer", str(ans_f))
        check("cmd_fork печатает generation-recheck об отставшем условии",
              "ОТСТАЁТ" in out.stdout
              and "recheck на момент генерации" in out.stdout,
              out.stdout[-400:] or out.stderr[-400:])


def section_postponement(kspi_basis):
    """Перенос сдвигает горизонт и роняет годовую доходность, не трогая коридор."""
    print("\n— перенос: горизонт растёт, доходность падает, коридор тот же —")
    kspi_forks, _ = fork.parse(fixture("fork_kspi_answer.json"))
    bull = next(x for x in kspi_forks if x.label == "bull")

    checks = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.TRIGGERED,
            fact="условие сработало"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.POSTPONED,
            fact="запуск Hepsi Bank перенесён на 2028"),
    ]
    outcome = recheck.check(bull, kspi_basis, checks)
    check("перенос создал отложенный форк", outcome.deferred is not None)

    deferred = outcome.deferred
    _eq("горизонт вырос на 12 мес",
        deferred.horizon_months, bull.horizon_months + 12)
    _eq("прежний горизонт сохранён",
        deferred.deferred_from, bull.horizon_months)

    # Коридор не меняется: те же предпосылки.
    orig_res = fork.evaluate(kspi_basis, bull)
    defer_res = fork.evaluate(kspi_basis, deferred)
    check("коридор при переносе тот же",
          orig_res.corridor == defer_res.corridor,
          f"было {orig_res.corridor}, стало {defer_res.corridor}")

    # Годовая доходность упала.
    check("годовая доходность к середине упала",
          outcome.new_expected["mid"][1] < orig_res.expected["mid"][1],
          f"было {orig_res.expected['mid'][1]:.4f}, "
          f"стало {outcome.new_expected['mid'][1]:.4f}")

    # Полная доходность та же.
    check("полная доходность к середине не изменилась",
          abs(outcome.new_expected["mid"][0] - orig_res.expected["mid"][0]) < 1e-9,
          f"было {orig_res.expected['mid'][0]:.6f}, "
          f"стало {outcome.new_expected['mid'][0]:.6f}")

    # Тест с двумя переносами (24 месяца).
    double_checks = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.POSTPONED,
            fact="снижение ставки отложено на год"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.POSTPONED,
            fact="запуск Hepsi Bank перенесён ещё на год"),
    ]
    double = recheck.check(bull, kspi_basis, double_checks)
    check("два переноса — горизонт +24 мес",
          double.deferred.horizon_months == bull.horizon_months + 24,
          f"ожидалось {bull.horizon_months + 24}, "
          f"получено {double.deferred.horizon_months}")


def section_failed_marks_fork(kspi_basis):
    """Несработавшее условие помечает форк требующим решения."""
    print("\n— несработавшее условие помечает форк —")
    kspi_forks, _ = fork.parse(fixture("fork_kspi_answer.json"))
    bull = next(x for x in kspi_forks if x.label == "bull")

    # Все сработали — требования решения нет.
    all_ok = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.TRIGGERED,
            fact="сработало"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.TRIGGERED,
            fact="сработало"),
    ]
    outcome = recheck.check(bull, kspi_basis, all_ok)
    check("все сработали — нет требования решения",
          not outcome.requires_decision)

    # Одно не сработало — требование решения.
    one_fail = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.NOT_TRIGGERED,
            fact="прибыль не выросла"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.TRIGGERED,
            fact="сработало"),
    ]
    outcome = recheck.check(bull, kspi_basis, one_fail)
    check("одно не сработало — форк требует решения",
          outcome.requires_decision)

    # Все не сработали — требование решения.
    all_fail = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.NOT_TRIGGERED,
            fact="не сработало"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.NOT_TRIGGERED,
            fact="не сработало"),
    ]
    outcome = recheck.check(bull, kspi_basis, all_fail)
    check("все не сработали — форк требует решения",
          outcome.requires_decision)

    # Пока не проверить — не требование решения.
    cant = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.CANT_CHECK,
            fact="данных пока нет"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.CANT_CHECK,
            fact="данных пока нет"),
    ]
    outcome = recheck.check(bull, kspi_basis, cant)
    check("пока не проверить — не требование решения",
          not outcome.requires_decision)


def section_thesis_unchanged(kspi_basis):
    """Попытка изменить тезис или условия в речеке отклоняется."""
    print("\n— попытка изменить тезис или условия отклоняется —")
    kspi_forks, _ = fork.parse(fixture("fork_kspi_answer.json"))
    bull = next(x for x in kspi_forks if x.label == "bull")

    # Другое число условий.
    wrong_count = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.TRIGGERED,
            fact="сработало"),
    ]
    outcome = recheck.check(bull, kspi_basis, wrong_count)
    check("меньше условий, чем в форке — отказ",
          outcome.is_refusal()
          and "не меняет набор условий" in str(outcome.refusals),
          str(outcome.refusals))

    # Изменённый текст условия.
    changed = [
        recheck.ConditionCheck(
            condition="совсем другое условие",
            status=recheck.TRIGGERED,
            fact="сработало"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.CANT_CHECK,
            fact="данных нет"),
    ]
    outcome = recheck.check(bull, kspi_basis, changed)
    check("изменённый текст условия — отказ",
          outcome.is_refusal()
          and "не меняет условия форка" in str(outcome.refusals),
          str(outcome.refusals))

    # Переставленные условия.
    swapped = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.TRIGGERED,
            fact="сработало"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.CANT_CHECK,
            fact="данных нет"),
    ]
    outcome = recheck.check(bull, kspi_basis, swapped)
    check("переставленные условия — отказ",
          outcome.is_refusal()
          and "изменено" in str(outcome.refusals),
          str(outcome.refusals))

    # Неизвестный статус.
    bad_status = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status="отменено",
            fact="сработало"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.CANT_CHECK,
            fact="данных нет"),
    ]
    outcome = recheck.check(bull, kspi_basis, bad_status)
    check("неизвестный статус — отказ",
          outcome.is_refusal()
          and "неизвестный статус" in str(outcome.refusals),
          str(outcome.refusals))

    # Факт из нескольких строк.
    multiline = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.TRIGGERED,
            fact="строка\nвторая строка"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.CANT_CHECK,
            fact="данных нет"),
    ]
    outcome = recheck.check(bull, kspi_basis, multiline)
    check("факт из нескольких строк — отказ",
          outcome.is_refusal()
          and "одной непустой строкой" in str(outcome.refusals),
          str(outcome.refusals))

    # Пустой факт.
    empty_fact = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.TRIGGERED,
            fact="   "),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.CANT_CHECK,
            fact="данных нет"),
    ]
    outcome = recheck.check(bull, kspi_basis, empty_fact)
    check("пустой факт — отказ",
          outcome.is_refusal()
          and "одной непустой строкой" in str(outcome.refusals),
          str(outcome.refusals))


def section_history(kspi_basis):
    """Результаты сверки пишутся в память и видны в истории."""
    print("\n— результаты видны в истории —")
    kspi_forks, _ = fork.parse(fixture("fork_kspi_answer.json"))
    bull = next(x for x in kspi_forks if x.label == "bull")
    bear = next(x for x in kspi_forks if x.label == "bear")

    # Запись bull (сработало + перенесено).
    bull_checks = [
        recheck.ConditionCheck(
            condition=bull.must_be_true[0],
            status=recheck.TRIGGERED,
            fact="ЦБ РК снизил ставку, как ожидалось"),
        recheck.ConditionCheck(
            condition=bull.must_be_true[1],
            status=recheck.POSTPONED,
            fact="запуск Hepsi Bank перенесён на 2028"),
    ]
    outcome = recheck.check(bull, kspi_basis, bull_checks)
    rid1 = recheck.record("KSPI", outcome)
    check("запись bull создана", rid1 > 0, f"id={rid1}")

    # Запись bear (не сработало + пока не проверить).
    bear_checks = [
        recheck.ConditionCheck(
            condition=bear.must_be_true[0],
            status=recheck.NOT_TRIGGERED,
            fact="прибыль выросла, а не упала"),
        recheck.ConditionCheck(
            condition=bear.must_be_true[1],
            status=recheck.CANT_CHECK,
            fact="NIM не раскрыт в 3К26"),
    ]
    outcome = recheck.check(bear, kspi_basis, bear_checks)
    rid2 = recheck.record("KSPI", outcome)
    check("запись bear создана", rid2 > 0, f"id={rid2}")

    # Чтение истории.
    events = recheck.timeline("KSPI")
    # CLI test (section_contract) also wrote records — at least 2 from this section.
    check("в истории есть записи", len(events) >= 2,
          f"получено {len(events)}")
    if len(events) >= 2:
        # Last two records are from this section (section_contract wrote the first two).
        bull_ev = events[-2]
        bear_ev = events[-1]
        _eq("метка bull в истории", bull_ev["fork_label"], "bull")
        _eq("метка bear в истории", bear_ev["fork_label"], "bear")
        check("bull не требует решения",
              not bull_ev["requires_decision"])
        check("bear требует решения",
              bear_ev["requires_decision"])
        _eq("перенос bull на 12 мес",
            bull_ev["deferred_months"], 12)
        _eq("новый горизонт bull",
            bull_ev["new_horizon_months"], bull.horizon_months + 12)
        check("ожидаемая доходность записана",
              bull_ev["expected"] is not None
              and "mid" in bull_ev["expected"],
              f"{bull_ev['expected']!r}")
        check("в истории видны статусы условий",
              all("status" in c and "condition" in c and "fact" in c
                  for c in bull_ev["conditions"]),
              f"{bull_ev['conditions']!r}")

    # Пустой тикер.
    empty = recheck.timeline("NONE")
    check("тикер без речеков — пустая история", empty == [], f"{empty!r}")


def section_contract():
    """Контракты слоя речека."""
    print("\n— контракты слоя —")
    src = open(Path(ROOT / "agent" / "recheck.py"), encoding="utf-8").read()
    check("recheck.py не ходит в сеть",
          not any(w in src for w in ("yfinance", "requests", "urllib", "http")))
    check("статусов четыре, не три",
          len(recheck.STATUSES) == 4, f"{recheck.STATUSES!r}")
    check("перенесено — четвёртый статус",
          recheck.POSTPONED in recheck.STATUSES,
          f"{recheck.STATUSES!r}")
    check("статусы те же, что в db.py",
          recheck.STATUSES == ("сработало", "не сработало",
                               "пока не проверить", "перенесено"),
          f"{recheck.STATUSES!r}")

    print("\n— команда считает речек из замороженных ответов —")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        facts = tmp / "kspi.json"
        facts.write_text(json.dumps(KSPI, ensure_ascii=False), encoding="utf-8")
        fork_answer = str(FIXTURES / "fork_kspi_answer.json")

        # Речек: bull сработало + перенесено, bear не сработало + пока не проверить.
        recheck_data = {
            "forks": [
                {"label": "bull", "conditions": [
                    {"condition": "ставка по вкладам в тенге в отчёте за 4кв26 ниже 14%",
                     "status": "сработало",
                     "fact": "ЦБ РК снизил ставку до 14.5% в ноябре 2026"},
                    {"condition": "объявленная выплата за 1кв27 выше 4400 KZT на акцию",
                     "status": "перенесено",
                     "fact": "запуск Hepsi Bank перенесён на 2028 — из отчёта за 3К26"},
                ]},
                {"label": "bear", "conditions": [
                    {"condition": "прибыль за 4кв26 ниже прибыли за 4кв25",
                     "status": "не сработало",
                     "fact": "прибыль Kaspi.kz за 3К26 выросла на 18% г/г"},
                    {"condition": "чистая процентная маржа не выросла",
                     "status": "пока не проверить",
                     "fact": "отчёт за 3К26 не раскрывает NIM — ждём годовой"},
                ]},
            ],
        }
        recheck_file = tmp / "recheck.json"
        recheck_file.write_text(json.dumps(recheck_data, ensure_ascii=False),
                                encoding="utf-8")

        bank = ("--facts", str(facts), "--rate", str(RATE), "--growth", str(GROWTH),
                "--rate-why", RATE_WHY, "--growth-why", GROWTH_WHY)
        out = run_cli("recheck", "KSPI", *bank,
                      "--fork-answer", fork_answer,
                      "--answer", str(recheck_file))

        check("команда отрабатывает", out.returncode == 0,
              f"returncode={out.returncode}, stderr={out.stderr.strip()[-300:]}")
        check("в выводе все четыре статуса",
              "сработало" in out.stdout and "не сработало" in out.stdout
              and "пока не проверить" in out.stdout
              and "перенесено" in out.stdout,
              out.stdout[:800])
        check("в выводе требование решения для bear",
              "требует решения" in out.stdout
              and "пересмотреть или закрыть" in out.stdout,
              out.stdout[:1000])
        check("в выводе перенос горизонта для bull",
              "перенос: горизонт вырос" in out.stdout
              and "годовая доходность ниже" in out.stdout,
              out.stdout[:1000])
        check("в выводе история речеков",
              "история речеков" in out.stdout, out.stdout[:1200])
        check("записано в историю",
              "записано в историю" in out.stdout, out.stdout[:1200])

        # Отказ: попытка изменить условие.
        bad_recheck = {
            "forks": [
                {"label": "bull", "conditions": [
                    {"condition": "совсем другое условие",
                     "status": "сработало",
                     "fact": "что-то"},
                    {"condition": "объявленная выплата за 1кв27 выше 4400 KZT на акцию",
                     "status": "перенесено",
                     "fact": "перенос"},
                ]},
            ],
        }
        bad_file = tmp / "bad_recheck.json"
        bad_file.write_text(json.dumps(bad_recheck, ensure_ascii=False),
                            encoding="utf-8")
        out = run_cli("recheck", "KSPI", *bank,
                      "--fork-answer", fork_answer,
                      "--answer", str(bad_file))
        check("попытка изменить условие — отказ с кодом 3",
              out.returncode == 3
              and "не меняет условия форка" in out.stdout,
              f"returncode={out.returncode}, out={out.stdout[:500]}")

        # Отказ: неизвестный тикер.
        out = run_cli("recheck", "NOPE", *bank,
                      "--fork-answer", fork_answer,
                      "--answer", str(recheck_file))
        check("неизвестный тикер — ошибка команды", out.returncode == 2,
              f"returncode={out.returncode}")


def _invest_state():
    path = ROOT / "invest.db"
    if not path.exists():
        return None
    st = path.stat()
    return st.st_size, st.st_mtime_ns


@patch("agent.baseline.prisms.gate", return_value=None)
def main(_prisms_gate) -> int:
    print("\nОфлайн-регрессия слоя 4 v2 — речек: сверка условий форков "
          "(agent/recheck.py)\n")
    before = _invest_state()
    with tempfile.TemporaryDirectory() as td:
        os.environ["AGENT_PROFILE_DB"] = str(Path(td) / "agent.db")
        try:
            kspi_base = baseline.build("KSPI", kspi_facts(), rate=RATE,
                                       growth=GROWTH, rate_why=RATE_WHY,
                                       growth_why=GROWTH_WHY)
            check("базовая линия банка считается до речека",
                  not kspi_base.is_refusal()
                  and kspi_base.corridor == KSPI_BASE_CORRIDOR,
                  "; ".join(m.message for m in kspi_base.mismatches))
            kspi_basis = fork.basis(kspi_base)

            section_statuses(kspi_basis)
            section_generation(kspi_basis)
            section_postponement(kspi_basis)
            section_failed_marks_fork(kspi_basis)
            section_thesis_unchanged(kspi_basis)
            # CLI tests before DB writes: section_contract spawns subprocess
            # that imports profile_store, which needs profile_events table.
            # Ensure the table exists before the subprocess runs.
            profile_store._connect().close()
            section_contract()
            section_history(kspi_basis)
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
    print(f"{GREEN}Все PASS{RESET} — речек сверяет условия, не меняя тезис; "
          f"перенос роняет доходность, не трогая коридор.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
