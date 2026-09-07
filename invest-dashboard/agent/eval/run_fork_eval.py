#!/usr/bin/env python3
"""Офлайн-регрессия слоя 2 v2 — форк как ветка базовой линии (SPC-008, agent/fork.py).

Форк наследует допущения базовой линии и переопределяет подмножество
предпосылок. Границы стоимости модель не объявляет: коридор считает тот же
доверенный код, что и базовую линию, поэтому «заявлено против пересчитано»
невозможно по построению.

Якорь — два поля: anchor_class (чей источник) и confirms (что источник
подтверждает: само число, драйвер, либо источника нет вовсе). Класс «суждение
аналитика» совместим только с «собственной оценкой» — иначе форк утверждает
несуществующий источник. Требование якоря адресное: подпёрта должна быть
величина, несущая тезис; полоса оценки без источника делает коридор
оценочным, но ставку не отменяет.

Каналов три — поток, прибыль, дивиденд. Выбор канала объявляется с
основанием: канал обязан мерить то, что меняется по тезису. Без этого модель
уходит в единственный канал, форма которого подана готовой, даже когда он для
этой бумаги неверен (DELL 24.08: механически взятая peer-полоса дала bull
ниже базовой линии).

Эвал гоняется на замороженных ответах модели (agent/eval/fixtures/) и
фикстурах фактов, без сети. Журнал профиля подменяется (AGENT_PROFILE_DB),
боевая invest.db не открывается.

    python3 agent/eval/run_fork_eval.py   # exit 0, если все PASS
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

from agent import baseline, coherence, fork, measures, profile  # noqa: E402

CLI = str(ROOT / "agent" / "cli.py")
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GREEN, RED, GREY, RESET = "\033[32m", "\033[31m", "\033[90m", "\033[0m"
FAILURES = []

# Допущения банка — те же, что в эвале слоя 1: ставка и рост объявлены с
# происхождением, слой их в константах не держит.
FX = 0.0021820245310664177
RATE, GROWTH = 0.17, 0.09
RATE_WHY = "ставка ЦБ РК плюс премия за акционерный риск, в тенге"
GROWTH_WHY = "инфляция Казахстана плюс реальный рост экономики"

# KSPI, отчёт за 2К26 — те же когерентные факты одного отчёта, что в слое 1
# (agent/eval/run_baseline_eval.py): базовая линия 117.51–118.77 при цене 105.55.
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

# DELL — мера levered; базовая линия считается тем же кодом measures.calculate.
# Числа подогнаны под случай прототипа: базовая линия около 224–272 при цене
# 479.81, peer-полоса SMCI 6.96 / HPE 12.81 при собственном 20.37x.
DELL_PRICE = 479.81
DELL_ASSUMPTIONS = {
    "fcf_base": 140.0, "shares": 10.0,
    "scenarios": {"base": {"growth_path": [0.10] * 5, "discount_rate": 0.10,
                           "terminal_growth": 0.02}},
}
DELL_BASE_CORRIDOR = (226.34, 275.27)

NBIS_ASSUMPTIONS = {
    "core": {
        "revenue_base": 3_000_000_000, "shares": 500_000_000,
        "net_cash": 1_000_000_000,
        "scenarios": {"base": {
            "growth_path": [1.0, 0.6, 0.35, 0.22, 0.15],
            "discount_rate": 0.10, "terminal_fcf_margin": 0.25,
            "terminal_fcf_multiple": 20.0}},
    },
    "stakes": [{"name": "ClickHouse", "ownership_pct": 0.28,
                "entity_valuation": {"base": 20_000_000_000}}],
}


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


def refusal_text(res):
    return " | ".join(res.refusals)


def mid(corridor):
    return (corridor[0] + corridor[1]) / 2


def run_cli(*args):
    """Команда в отдельном процессе: тот же слой, входы из файлов."""
    return subprocess.run(_isolated_script(CLI, *args), capture_output=True,
                          text=True, cwd=ROOT)


def _isolated_script(script, *args):
    source = (
        "import runpy\n"
        "from unittest.mock import patch\n"
        "with patch('agent.cli.prisms.gate', return_value=None), "
        "patch('agent.baseline.prisms.gate', return_value=None):\n"
        f"    runpy.run_path({str(script)!r}, run_name='__main__')\n"
    )
    return [sys.executable, "-c", source, *args]


def section_parse():
    """Ответ модели → форки. Форму проверяет код, а не текст роли."""
    print("\n— замороженный ответ разбирается в форки —")
    dell, problems = fork.parse(fixture("fork_dell_answer.json"))
    _eq("у DELL два форка, проблем формы нет", (len(dell), problems), (2, ()))
    _eq("метки bull и bear", [f.label for f in dell], ["bull", "bear"])
    _eq("каналы объявлены", [f.channel for f in dell],
        [fork.EARNINGS, fork.CASH_FLOW])
    check("основание канала объявлено: канал меряет то, что меняется",
          all(f.channel_reason.strip() for f in dell),
          str([f.channel_reason for f in dell]))
    _eq("условия проверки перенесены",
        [len(f.must_be_true) for f in dell], [2, 2])
    _eq("переопределений по числу полей канала",
        [sorted(f.overrides) for f in dell],
        [["eps_forward", "pe_high", "pe_low"], ["discount_rate", "growth_path",
                                                "terminal_growth"]])

    kspi, problems = fork.parse(fixture("fork_kspi_answer.json"))
    _eq("замороженный ответ в fenced-блоке разбирается так же",
        (len(kspi), problems), (2, ()))
    _eq("каналы банка", [f.channel for f in kspi],
        [fork.DIVIDEND, fork.EARNINGS])

    print("\n— объявленные границы стоимости отклоняются —")
    declared = json.dumps({"forks": [{
        "label": "bull", "thesis": "прибыль растёт",
        "channel": "прибыль", "channel_reason": "меняется прибыль",
        "horizon_months": 12, "must_be_true": ["прибыль растёт"],
        "corridor": [190.0, 243.39],
        "overrides": {"eps_forward": {
            "value": 19.0, "rationale": "рост", "anchor_class": "company_guide",
            "confirms": "driver", "source": "гайд"}}}]}, ensure_ascii=False)
    forks, problems = fork.parse(declared)
    _eq("форк с коридором не доходит до расчёта", forks, ())
    codes = [p.code for p in problems]
    check("отказ — про объявленную границу, а не про форму остального",
          "bounds" in codes, f"{problems!r}")
    check("отказ называет поле, которым объявлена граница",
          any("corridor" in p.message for p in problems), f"{problems!r}")
    check("отказ говорит, что границы считает код",
          any("считает код" in p.message for p in problems), f"{problems!r}")
    target, problems = fork.parse(json.dumps({"forks": [{
        "label": "bull", "thesis": "т", "channel": "прибыль",
        "channel_reason": "р", "horizon_months": 12, "must_be_true": ["у"],
        "target_price": 300.0, "overrides": {}}]}, ensure_ascii=False))
    check("названная цель цены — та же объявленная граница",
          not target and any("target_price" in p.message for p in problems),
          f"{problems!r}")
    empty, problems = fork.parse(json.dumps({"forks": []}))
    check("ответ без форков — отказ, а не пустой расчёт",
          not empty and any(p.code == "answer" for p in problems),
          f"{problems!r}")


def section_structural(basis):
    """Структурные отказы: форк, который нельзя посчитать."""
    print("\n— структурные отказы —")

    def ov(value, anchor, confirms, source="источник"):
        return fork.Override(value=value, rationale="обоснование",
                             anchor_class=anchor, source=source, confirms=confirms)

    def forker(**kw):
        raw = dict(label="bull", thesis="тезис", channel=fork.EARNINGS,
                   channel_reason="меняется прибыль", horizon_months=12,
                   must_be_true=("условие",), overrides={})
        raw.update(kw)
        return fork.Fork(**raw)

    cases = [
        ("пустой тезис", forker(thesis="   "),
         "тезис пуст"),
        ("нет условий проверки", forker(must_be_true=()),
         "проверяемых условий"),
        ("нет ни одного переопределения", forker(overrides={}),
         "не отличается от базовой линии"),
        ("чужая метка", forker(label="base"), "bull и bear"),
        ("канал не объявлен", forker(channel=""),
         "канал"),
        ("канал без основания", forker(channel_reason=""),
         "мерить то, что меняется"),
        ("неизвестный канал", forker(channel="ставка"),
         "поток"),
    ]
    for name, f, needle in cases:
        res = fork.evaluate(basis, f)
        check(f"{name} — отказ", res.is_refusal(), refusal_text(res))
        check(f"  {name}: отказ называет причину", needle in refusal_text(res),
              refusal_text(res))
        check(f"  {name}: коридора в отказе нет", res.corridor is None,
              f"{res.corridor!r}")

    print("\n— переопределяется предпосылка, а не граница стоимости —")
    res = fork.evaluate(basis, forker(overrides={
        "fair_value": ov(300.0, fork.COMPANY_GUIDE, fork.NUMBER)}))
    check("поле вне списка предпосылок — отказ", res.is_refusal(),
          refusal_text(res))
    check("отказ перечисляет, что переопределяется",
          "eps_forward" in refusal_text(res) and "dps_forward" in refusal_text(res),
          refusal_text(res))

    print("\n— поля канала объявляются вместе —")
    res = fork.evaluate(basis, forker(overrides={
        "eps_forward": ov(19.0, fork.COMPANY_GUIDE, fork.DRIVER)}))
    check("eps_forward без полосы — отказ", res.is_refusal(), refusal_text(res))
    check("отказ называет недостающие поля полосы",
          "pe_low" in refusal_text(res) and "pe_high" in refusal_text(res),
          refusal_text(res))

    print("\n— якорь из двух полей —")
    res = fork.evaluate(basis, forker(overrides={
        "eps_forward": ov(19.0, fork.ANALYST_JUDGEMENT, fork.NUMBER),
        "pe_low": ov(6.96, fork.PEER_STATS, fork.NUMBER),
        "pe_high": ov(12.81, fork.PEER_STATS, fork.NUMBER)}))
    check("суждение аналитика с подтверждённым числом отклоняется",
          res.is_refusal(), refusal_text(res))
    check("отказ объясняет подмену: класса источника нет, а подтверждение объявлено",
          "источника нет" in refusal_text(res) and "number" in refusal_text(res),
          refusal_text(res))
    check("коридор не считается — отказ, а не число с чужим якорем",
          res.corridor is None, f"{res.corridor!r}")

    res = fork.evaluate(basis, forker(overrides={
        "eps_forward": ov(19.0, fork.PEER_STATS, fork.NUMBER, source=""),
        "pe_low": ov(6.96, fork.PEER_STATS, fork.NUMBER),
        "pe_high": ov(12.81, fork.PEER_STATS, fork.NUMBER)}))
    check("якорённый класс без источника — отказ", res.is_refusal(),
          refusal_text(res))
    res = fork.evaluate(basis, forker(overrides={
        "eps_forward": ov(19.0, fork.COMPANY_GUIDE, fork.DRIVER, source=""),
        "pe_low": ov(6.96, fork.PEER_STATS, fork.NUMBER),
        "pe_high": ov(12.81, fork.PEER_STATS, fork.NUMBER)}))
    check("число, выведенное из драйвера, источника требует",
          res.is_refusal(), refusal_text(res))

    res = fork.evaluate(basis, forker(overrides={
        "eps_forward": ov(19.0, fork.PEER_STATS, fork.ESTIMATE,
                          source="полоса сопоставимых"),
        "pe_low": ov(6.96, fork.PEER_STATS, fork.NUMBER),
        "pe_high": ov(12.81, fork.PEER_STATS, fork.NUMBER)}))
    check("названный источник с признаком «источника нет» отклоняется — "
          "правило работает в обе стороны",
          res.is_refusal() and "PEER_STATS" in refusal_text(res).upper(),
          refusal_text(res))

    print("\n— предпосылка, на которой каналу нечего считать —")
    res = fork.evaluate(basis, forker(overrides={
        "eps_forward": ov(-3.0, fork.COMPANY_GUIDE, fork.DRIVER),
        "pe_low": ov(6.96, fork.PEER_STATS, fork.NUMBER),
        "pe_high": ov(12.81, fork.PEER_STATS, fork.NUMBER)}))
    check("убыточная база в канале прибыли — отказ, а не минус в коридоре",
          res.is_refusal() and "положительная" in refusal_text(res),
          refusal_text(res))
    res = fork.evaluate(basis, forker(overrides={
        "eps_forward": ov("19.0", fork.COMPANY_GUIDE, fork.DRIVER),
        "pe_low": ov(6.96, fork.PEER_STATS, fork.NUMBER),
        "pe_high": ov(12.81, fork.PEER_STATS, fork.NUMBER)}))
    check("число строкой не доходит до арифметики", res.is_refusal(),
          refusal_text(res))

    print("\n— терминал выше потолка без макро-якоря —")
    res = fork.evaluate(basis, forker(channel=fork.CASH_FLOW,
                                      channel_reason="меняется темп потока",
                                      overrides={
        "growth_path": ov([0.1] * 5, fork.COMPANY_GUIDE, fork.DRIVER),
        "terminal_growth": ov(0.05, fork.COMPANY_GUIDE, fork.DRIVER)}))
    check("терминал 5% выше потолка 4% — отказ", res.is_refusal(),
          refusal_text(res))
    check("отказ называет потолок и класс якоря",
          "4" in refusal_text(res) and "macro_guidance" in refusal_text(res),
          refusal_text(res))


def section_channels(basis, kspi_basis):
    """Канал объявляет, что меряет тезис; коридор считает доверенный код."""
    print("\n— канал меряет то, что меняется по тезису —")
    dell, _ = fork.parse(fixture("fork_dell_answer.json"))
    bear = next(f for f in dell if f.label == "bear")
    res = fork.evaluate(basis, bear)
    check("канал потока считается по переопределённым предпосылкам",
          not res.is_refusal(), refusal_text(res))
    _eq("коридор bear — тот же код, что у базовой линии, на её предпосылках",
        res.corridor, (138.32, 160.90))
    check("коридор ниже базовой линии — bear остался bear",
          mid(res.corridor) < mid(basis.corridor),
          f"{mid(res.corridor)} против {mid(basis.corridor)}")
    check("переопределённая ставка попала в расчёт",
          res.corridor != DELL_BASE_CORRIDOR, f"{res.corridor!r}")

    print("\n— канал и переопределяемые поля не расходятся —")
    wrong = fork.Fork(label="bull", thesis="тезис", channel=fork.DIVIDEND,
                      channel_reason="тезис про выплату", horizon_months=12,
                      must_be_true=("условие",),
                      overrides={
                          "eps_forward": fork.Override(
                              19.0, "рост", fork.COMPANY_GUIDE, "гайд",
                              confirms=fork.DRIVER),
                          "pe_low": fork.Override(
                              6.96, "низ полосы", fork.PEER_STATS, "SMCI",
                              confirms=fork.NUMBER),
                          "pe_high": fork.Override(
                              12.81, "верх полосы", fork.PEER_STATS, "HPE",
                              confirms=fork.NUMBER)})
    res = fork.evaluate(basis, wrong)
    check("канал дивиденд при переопределении прибыли — отказ", res.is_refusal(),
          refusal_text(res))
    check("отказ называет оба канала: объявленный и тот, чьё поле тронуто",
          "дивиденд" in refusal_text(res) and "прибыль" in refusal_text(res),
          refusal_text(res))

    print("\n— канала потока у банка нет: мера несёт другие предпосылки —")
    bank_flow = fork.Fork(label="bear", thesis="тезис", channel=fork.CASH_FLOW,
                          channel_reason="меняется поток", horizon_months=12,
                          must_be_true=("условие",),
                          overrides={
                              "growth_path": fork.Override(
                                  [0.03] * 5, "поток замедляется",
                                  fork.COMPANY_GUIDE, "гайд",
                                  confirms=fork.DRIVER),
                              "discount_rate": fork.Override(
                                  0.19, "премия", fork.ANALYST_JUDGEMENT, "",
                                  confirms=fork.ESTIMATE),
                              "terminal_growth": fork.Override(
                                  0.02, "ВВП", fork.MACRO_GUIDANCE, "ВВП США",
                                  confirms=fork.NUMBER)})
    res = fork.evaluate(kspi_basis, bank_flow)
    check("поток у банка — отказ, а не FCF, которого нет", res.is_refusal(),
          refusal_text(res))
    check("отказ называет меру базовой линии и её причину",
          "ddm_ri" in refusal_text(res) and "FCF" in refusal_text(res),
          refusal_text(res))
    return res


def section_anchors(basis, kspi_basis):
    """Адресное требование якоря: несущую величину подпирают, полосу — нет."""
    print("\n— полоса без источника: коридор оценочный, ставка в силе —")
    dell, _ = fork.parse(fixture("fork_dell_answer.json"))
    bear = next(f for f in dell if f.label == "bear")
    res = fork.evaluate(basis, bear)
    _eq("ставка без источника — единственная оценочная граница",
        res.estimated, ("discount_rate",))
    check("несущая величина подпёрта — форк остаётся ставкой",
          res.bettable and fork.is_bettable(bear),
          f"bettable={res.bettable}")
    check("коридор посчитан: оценочная полоса его не отменяет",
          res.corridor is not None, f"{res.corridor!r}")
    check("драйвер, выведенный аналитиком, показан отдельно",
          res.derived == ("growth_path",), f"{res.derived!r}")

    anchored = fork.Fork(label="bull", thesis="тезис", channel=fork.EARNINGS,
                         channel_reason="меняется прибыль", horizon_months=12,
                         must_be_true=("условие",),
                         overrides={
                             "eps_forward": fork.Override(
                                 19.0, "рост", fork.COMPANY_GUIDE, "гайд",
                                 confirms=fork.DRIVER),
                             "pe_low": fork.Override(
                                 6.96, "низ", fork.ANALYST_JUDGEMENT, "",
                                 confirms=fork.ESTIMATE),
                             "pe_high": fork.Override(
                                 12.81, "верх", fork.ANALYST_JUDGEMENT, "",
                                 confirms=fork.ESTIMATE)})
    res = fork.evaluate(basis, anchored)
    check("прибыль подпёрта, полоса нет — ставка не отменяется", res.bettable,
          f"bettable={res.bettable}, estimated={res.estimated}")
    _eq("оценочными помечены обе границы полосы",
        res.estimated, ("pe_low", "pe_high"))
    _eq("коридор посчитан из предпосылок: 19.0 × 6.96–12.81",
        res.corridor, (132.24, 243.39))

    print("\n— форк без несущей величины тезис не несёт —")
    rerating = fork.Fork(label="bull", thesis="тезис", channel=fork.CASH_FLOW,
                         channel_reason="меняется ставка", horizon_months=12,
                         must_be_true=("условие",),
                         overrides={
                             "discount_rate": fork.Override(
                                 0.09, "ставка ниже", fork.MACRO_GUIDANCE,
                                 "ставка ЦБ", confirms=fork.NUMBER),
                             "terminal_growth": fork.Override(
                                 0.02, "ВВП", fork.MACRO_GUIDANCE, "ВВП США",
                                 confirms=fork.NUMBER)})
    res = fork.evaluate(basis, rerating)
    check("переопределены одни полосы: коридор считается, но это не отказ",
          not res.is_refusal() and res.corridor is not None,
          refusal_text(res))
    check("несущая величины нет — форк остаётся наблюдением",
          not res.bettable and not fork.is_bettable(rerating),
          f"bettable={res.bettable}")

    print("\n— форк без якоря на несущей величине остаётся наблюдением —")
    kspi, _ = fork.parse(fixture("fork_kspi_answer.json"))
    bear = next(f for f in kspi if f.label == "bear")
    res = fork.evaluate(kspi_basis, bear)
    check("прибыль на суждении — не отказ: форк можно читать", not res.is_refusal(),
          refusal_text(res))
    check("но ставкой он не становится", not res.bettable
          and not fork.is_bettable(bear), f"bettable={res.bettable}")
    _eq("коридор всё равно посчитан из объявленных предпосылок",
        res.corridor, (41.89, 62.84))
    _eq("оценочная полоса помечена", res.estimated, ("pe_low", "pe_high"))
    check("несущая величина в списке без якоря",
          "eps_forward" in res.unanchored, f"{res.unanchored!r}")

    print("\n— SPC-009 §1.3: has_fact_anchor требует confirms и источник, не только класс —")
    labeled_but_estimated = fork.Fork(
        label="bear", thesis="риск клиента реализуется", channel=fork.EARNINGS,
        channel_reason="меняется прибыль", horizon_months=12,
        must_be_true=("клиентская концентрация подтвердилась",),
        overrides={
            "eps_forward": fork.Override(
                4.0, "конечная оценка аналитика", fork.COMPANY_GUIDE,
                "отчёт", confirms=fork.ESTIMATE),
            "pe_low": fork.Override(
                6.96, "низ", fork.PEER_STATS, "SMCI", confirms=fork.NUMBER),
            "pe_high": fork.Override(
                12.81, "верх", fork.PEER_STATS, "HPE", confirms=fork.NUMBER)})
    res = fork.evaluate(basis, labeled_but_estimated)
    check("company_guide с confirms=estimate — противоречие ловится ещё на validate()",
          res.is_refusal() and "confirms" in refusal_text(res), refusal_text(res))
    check("прямой вызов is_bettable на несогласованном форке — тоже не ставка "
          "(до validate() эта ветка не должна давать True вслепую)",
          not fork.is_bettable(labeled_but_estimated),
          f"is_bettable={fork.is_bettable(labeled_but_estimated)}")

    risk_anchored = fork.Fork(
        label="bear", thesis="клиентская концентрация давит на выручку",
        channel=fork.EARNINGS, channel_reason="меняется прибыль",
        horizon_months=12,
        must_be_true=("топ-клиент сокращает закупки",),
        overrides={
            "eps_forward": fork.Override(
                4.0, "выведено из факта риска отчётности",
                fork.COMPANY_GUIDE, "10-K: концентрация 40% выручки на "
                "одном клиенте", confirms=fork.DRIVER),
            "pe_low": fork.Override(
                6.96, "низ", fork.PEER_STATS, "SMCI", confirms=fork.NUMBER),
            "pe_high": fork.Override(
                12.81, "верх", fork.PEER_STATS, "HPE", confirms=fork.NUMBER)})
    res = fork.evaluate(basis, risk_anchored)
    check("EPS выведен из факта риска (confirms=driver) — ставка в силе",
          res.bettable and fork.is_bettable(risk_anchored),
          f"bettable={res.bettable}")

    # has_fact_anchor проверяется в изоляции (без validate()): старый is_bettable
    # смотрел только anchor_class != analyst_judgement и пропустил бы оба случая.
    check("class ANCHORED + confirms=estimate — не факт-якорь даже при source",
          not fork.has_fact_anchor(fork.Override(
              4.0, "р", fork.COMPANY_GUIDE, "источник указан",
              confirms=fork.ESTIMATE)))
    check("class ANCHORED + confirms=driver + непустой источник — факт-якорь",
          fork.has_fact_anchor(fork.Override(
              4.0, "р", fork.COMPANY_GUIDE, "10-K",
              confirms=fork.DRIVER)))

    unsourced = fork.Fork(
        label="bear", thesis="риск клиента", channel=fork.EARNINGS,
        channel_reason="меняется прибыль", horizon_months=12,
        must_be_true=("клиент уходит",),
        overrides={
            "eps_forward": fork.Override(
                4.0, "выведено из факта", fork.COMPANY_GUIDE, "  ",
                confirms=fork.DRIVER),
            "pe_low": fork.Override(
                6.96, "низ", fork.PEER_STATS, "SMCI", confirms=fork.NUMBER),
            "pe_high": fork.Override(
                12.81, "верх", fork.PEER_STATS, "HPE", confirms=fork.NUMBER)})
    check("confirms=driver с пустым source якорем не считается",
          not fork.has_fact_anchor(unsourced.overrides["eps_forward"]))


def section_direction(basis, kspi_basis):
    """Направление проверяется по серединам: границы перекрываются всегда."""
    print("\n— bull ниже базовой линии: сравниваются середины —")
    dell, _ = fork.parse(fixture("fork_dell_answer.json"))
    bull = next(f for f in dell if f.label == "bull")
    res = fork.evaluate(basis, bull)
    check("это замечание, а не отказ: якорь не отменяется", not res.is_refusal(),
          refusal_text(res))
    check("bull с серединой ниже середины базовой линии замечен",
          len(res.warnings) == 1, f"{res.warnings!r}")
    f = res.warnings[0]
    check("замечание называет метку, обе середины и разрыв",
          "bull" in f and "187.81" in f and "250.81" in f and "25%" in f, f)
    check("границы при этом перекрываются: по границам дефект не ловится",
          res.corridor[1] > basis.corridor[0]
          and mid(res.corridor) < mid(basis.corridor),
          f"форк {res.corridor} против базовой {basis.corridor}")
    check("замечание говорит, что чинить: метку или заниженную полосу",
          "премия" in f, f)

    print("\n— bear выше базовой линии — то же замечание —")
    up = fork.Fork(label="bear", thesis="тезис", channel=fork.EARNINGS,
                   channel_reason="меняется прибыль", horizon_months=12,
                   must_be_true=("условие",),
                   overrides={
                       "eps_forward": fork.Override(
                           19.0, "рост", fork.COMPANY_GUIDE, "гайд",
                           confirms=fork.DRIVER),
                       "pe_low": fork.Override(20.0, "низ", fork.PEER_STATS,
                                               "сопоставимые",
                                               confirms=fork.NUMBER),
                       "pe_high": fork.Override(30.0, "верх", fork.PEER_STATS,
                                                "сопоставимые",
                                                confirms=fork.NUMBER)})
    res = fork.evaluate(basis, up)
    check("bear с серединой выше середины базовой линии замечен",
          len(res.warnings) == 1 and "bear" in res.warnings[0],
          f"{res.warnings!r}")

    print("\n— направление в согласии с меткой замечания не даёт —")
    kspi, _ = fork.parse(fixture("fork_kspi_answer.json"))
    bank_bull = next(x for x in kspi if x.label == "bull")
    res = fork.evaluate(kspi_basis, bank_bull)
    _eq("коридор канала дивиденд — точка: дивиденд на требуемую доходность",
        res.corridor, (167.29, 167.29))
    check("bull выше базовой линии — замечаний нет", res.warnings == (),
          f"{res.warnings!r}")
    check("дивиденд подпёрт источником — ставка в силе", res.bettable,
          f"bettable={res.bettable}")
    check("дивиденд, выведенный аналитиком из драйвера компании, виден",
          res.derived == ("dps_forward",), f"{res.derived!r}")

    print("\n— унаследованный дивиденд не конвертируется второй раз —")
    inherited = fork.Fork(
        label="bull", thesis="требуемая доходность по бумаге ниже",
        channel=fork.DIVIDEND, channel_reason="меняется требуемая доходность",
        horizon_months=12, must_be_true=("условие",),
        overrides={"required_yield": fork.Override(
            0.06, "история бумаги", fork.OWN_HISTORY,
            "дивдоходность KSPI 2024–2026", confirms=fork.NUMBER)})
    res = fork.evaluate(kspi_basis, inherited)
    _eq("выплата взята у базовой линии уже в валюте котировки: 8.72 / 0.06",
        res.corridor, (145.33, 145.33))
    check("курс не умножен дважды: уровень порядка базовой линии, а не копеек",
          res.corridor[0] > kspi_basis.corridor[0] / 2, f"{res.corridor!r}")


def section_sotp():
    """SOTP-форк не теряет cash и доли через EPS × PE."""
    print("\n— SOTP-форк пересчитывает core и стейки целиком —")
    base = baseline.build("NBIS", assumptions=NBIS_ASSUMPTIONS)
    check("базовый SOTP считается", not base.is_refusal(),
          f"{getattr(base, 'errors', ())!r}")
    nbis_basis = fork.basis(base, price=100.0, price_currency="USD")

    def ov(value, source="сценарное допущение"):
        return fork.Override(value, "сценарий", fork.ANALYST_JUDGEMENT,
                             source, confirms=fork.ESTIMATE)

    earnings = fork.Fork(
        label="bear", thesis="слабее прибыль", channel=fork.EARNINGS,
        channel_reason="меняется EPS", horizon_months=12,
        must_be_true=("прибыль ниже плана",), overrides={
            "eps_forward": ov(0.1), "pe_low": ov(18.0), "pe_high": ov(30.0)})
    res = fork.evaluate(nbis_basis, earnings)
    check("канал прибыль для SOTP запрещён", res.is_refusal()
          and "SOTP" in refusal_text(res), refusal_text(res))

    def branch(label, growth, margin, multiple, rate, ownership, valuation):
        return fork.Fork(
            label=label, thesis="мощности и стоимость доли меняются",
            channel=fork.CASH_FLOW, channel_reason="пересчитывается полный SOTP",
            horizon_months=18, must_be_true=("ввод мощностей по плану",),
            overrides={
                "growth_path": ov(growth),
                "terminal_fcf_margin": ov(margin),
                "terminal_fcf_multiple": ov(multiple),
                "discount_rate": ov(rate),
                "stakes": ov([{"name": "ClickHouse",
                               "ownership_pct": ownership,
                               "entity_valuation": {"base": valuation}}]),
            })

    bull = fork.evaluate(nbis_basis, branch(
        "bull", [1.1, .7, .4, .25, .18], .28, 22.0, .09, .30, 24e9))
    bear = fork.evaluate(nbis_basis, branch(
        "bear", [.5, .3, .2, .12, .08], .18, 14.0, .13, .25, 16e9))
    check("оба SOTP-форка считаются через доверенный путь",
          not bull.is_refusal() and not bear.is_refusal(),
          f"bull={refusal_text(bull)}; bear={refusal_text(bear)}")
    expected_bull = json.loads(json.dumps(NBIS_ASSUMPTIONS))
    expected_bull["core"]["scenarios"]["base"].update({
        "growth_path": [1.1, .7, .4, .25, .18],
        "terminal_fcf_margin": .28, "terminal_fcf_multiple": 22.0,
        "discount_rate": .09})
    expected_bull["stakes"] = [{"name": "ClickHouse", "ownership_pct": .30,
                                "entity_valuation": {"base": 24e9}}]
    check("коридор совпадает с прямым measures.calculate полного SOTP",
          bull.corridor == measures.calculate("NBIS", expected_bull).corridor,
          f"fork={bull.corridor}, direct="
          f"{measures.calculate('NBIS', expected_bull).corridor}")
    check("bull выше base", bool(bull.corridor)
          and mid(bull.corridor) > mid(base.corridor),
          f"bull={bull.corridor}, base={base.corridor}")
    stake_floor = .25 * 16e9 / NBIS_ASSUMPTIONS["core"]["shares"]
    check("bear не ниже стоимости стейков",
          bool(bear.corridor) and bear.corridor[0] >= stake_floor,
          f"bear={bear.corridor}, стейки на акцию={stake_floor}")


def section_risk(kspi_basis):
    """Риск — перенос срока: коридор тот же, годовая доходность падает."""
    print("\n— перенос срока: коридор не меняется, доходность падает —")
    kspi, _ = fork.parse(fixture("fork_kspi_answer.json"))
    bull = next(x for x in kspi if x.label == "bull")
    _eq("горизонт из замороженного ответа", bull.horizon_months, 18)
    moved = fork.defer(bull, 12)
    _eq("горизонт вырос на перенос", moved.horizon_months, 30)
    _eq("прежний горизонт остался в форке", moved.deferred_from, 18)
    check("тезис и предпосылки при переносе не переписываются",
          moved.overrides == bull.overrides and moved.thesis == bull.thesis
          and moved.must_be_true == bull.must_be_true, f"{moved!r}")

    level = fork.evaluate(kspi_basis, bull).corridor
    before = fork.expected_return(kspi_basis.price, level, 18)
    after = fork.expected_return(kspi_basis.price,
                                 fork.evaluate(kspi_basis, moved).corridor,
                                 moved.horizon_months)
    check("перенос не трогает коридор: он считается по тем же предпосылкам",
          fork.evaluate(kspi_basis, moved).corridor
          == fork.evaluate(kspi_basis, bull).corridor, "")
    check("доходность к середине падает при том же коридоре",
          after["mid"][1] < before["mid"][1],
          f"{before['mid'][1]:.2%} → {after['mid'][1]:.2%}")
    check("полная доходность та же — срок перенесён, тезис не сломан",
          abs(after["mid"][0] - before["mid"][0]) < 1e-9,
          f"{before['mid'][0]:.4f} против {after['mid'][0]:.4f}")
    _eq("горизонт в годах попал в результат", after["years"], 2.5)
    band = fork.expected_return(100.0, (100.0, 200.0), 12)
    check("доходность считается и к границам коридора",
          band["low"][1] < band["mid"][1] < band["high"][1], f"{band!r}")


def section_paid_in(kspi_basis):
    """Доля оплаченного тезиса: отрицательного числа не бывает."""
    print("\n— доля оплаченного тезиса —")
    kspi, _ = fork.parse(fixture("fork_kspi_answer.json"))
    bull = next(x for x in kspi if x.label == "bull")
    res = fork.evaluate(kspi_basis, bull)
    paid = fork.paid_in(kspi_basis.price, kspi_basis.corridor, res.corridor)
    check("цена ниже базовой линии не даёт отрицательной доли",
          paid.share == 0.0 and paid.share is not None, f"{paid!r}")
    check("вместо доли — утверждение «тезис не оплачен»",
          "не оплачен" in paid.label, f"{paid.label!r}")
    check("утверждение называет, насколько цена ниже базовой линии",
          "11%" in paid.label, f"{paid.label!r}")

    bear = next(x for x in kspi if x.label == "bear")
    res = fork.evaluate(kspi_basis, bear)
    paid = fork.paid_in(kspi_basis.price, kspi_basis.corridor, res.corridor)
    check("bear-тезис тоже считается долей, а не знаком",
          0 < paid.share < 1, f"{paid!r}")
    check("доля названа в выводе", "оплатил" in paid.label, f"{paid.label!r}")

    paid = fork.paid_in(350.0, (100.0, 200.0), (250.0, 350.0))
    check("цена за пределами форка — тезис оплачен целиком и сверх",
          paid.share > 1 and "сверх" in paid.label, f"{paid!r}")
    paid = fork.paid_in(150.0, (100.0, 200.0), (100.0, 200.0))
    check("форк на базовой линии доли не имеет", paid.share is None
          and paid.label, f"{paid!r}")


def section_contract():
    print("\n— контракты слоя —")
    src = open(Path(ROOT / "agent" / "fork.py"), encoding="utf-8").read()
    check("fork.py не ходит в сеть",
          not any(w in src for w in ("yfinance", "requests", "urllib", "http")))
    check("форк объявляет три канала, четвёртого нет",
          fork.CHANNELS == (fork.CASH_FLOW, fork.EARNINGS, fork.DIVIDEND),
          f"{fork.CHANNELS!r}")
    check("несущие величины — прибыль, дивиденд, темп роста",
          fork.DRIVING_FIELDS == ("eps_forward", "dps_forward", "growth_path"),
          f"{fork.DRIVING_FIELDS!r}")
    check("полоса оценки и ставка несущей величиной не являются",
          not set(fork.BAND_FIELDS) & set(fork.DRIVING_FIELDS),
          f"{fork.BAND_FIELDS!r}")

    print("\n— команда считает форк из замороженного ответа —")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        facts = tmp / "kspi.json"
        facts.write_text(json.dumps(KSPI, ensure_ascii=False), encoding="utf-8")
        assumptions = tmp / "dell.json"
        assumptions.write_text(json.dumps(DELL_ASSUMPTIONS, ensure_ascii=False),
                               encoding="utf-8")
        dell_facts = tmp / "dell_facts.json"
        dell_facts.write_text(json.dumps({"price_context": {
            "earnings_data_stale": False,
            "most_recent_earnings_date": "2026-06-30",
        }}), encoding="utf-8")
        answer = str(FIXTURES / "fork_kspi_answer.json")
        bank = ("--facts", str(facts), "--rate", str(RATE), "--growth", str(GROWTH),
                "--rate-why", RATE_WHY, "--growth-why", GROWTH_WHY)
        out = run_cli("fork", "KSPI", *bank, "--answer", answer)
        check("команда отрабатывает", out.returncode == 0, out.stderr.strip()[-300:])
        check("в выводе метка, тезис и канал с основанием",
              "bull" in out.stdout and "дивиденд" in out.stdout
              and "channel_reason" not in out.stdout, out.stdout[:500])
        check("в выводе коридор канала и цена против базовой линии",
              "167.29" in out.stdout and "118.77" in out.stdout, out.stdout[:600])
        check("в выводе переопределения с якорем из двух полей",
              "company_guide" in out.stdout and "driver" in out.stdout
              and "own_history" in out.stdout, out.stdout[:800])
        check("в выводе — что тезис не оплачен", "не оплачен" in out.stdout,
              out.stdout[:800])
        check("в выводе — что форк ставка, а что наблюдение",
              "НАБЛЮДЕНИЕ" in out.stdout and "ставка: несущие" in out.stdout,
              out.stdout[-400:])
        check("в выводе замечаний направления нет, когда направление верно",
              "ниже базовой линии" not in out.stdout, out.stdout[:900])

        out = run_cli("fork", "DELL", "--assumptions", str(assumptions),
                      "--facts", str(dell_facts),
                      "--price", str(DELL_PRICE), "--quote-currency", "USD",
                      "--answer", str(FIXTURES / "fork_dell_answer.json"))
        check("мера levered считается форком через объявленные предпосылки",
              out.returncode == 0, out.stderr.strip()[-300:])
        check("замечание bull ниже базовой линии напечатано",
              "ниже базовой линии" in out.stdout and "187.81" in out.stdout,
              out.stdout[:600])
        check("оценочная полоса помечена в выводе",
              "оценочн" in out.stdout, out.stdout[:900])

        out = run_cli("fork", "DELL", "--assumptions", str(assumptions),
                      "--facts", str(dell_facts),
                      "--answer", str(FIXTURES / "fork_dell_answer.json"))
        check("без цены команда не считает: направление и доля не видны",
              out.returncode == 2 and "цены" in out.stderr,
              f"returncode={out.returncode}, stderr={out.stderr.strip()[-200:]}")

        declared = tmp / "declared.json"
        declared.write_text(json.dumps({"forks": [{
            "label": "bull", "thesis": "т", "channel": "прибыль",
            "channel_reason": "р", "horizon_months": 12, "must_be_true": ["у"],
            "corridor": [1.0, 2.0], "overrides": {}}]}, ensure_ascii=False),
            encoding="utf-8")
        out = run_cli("fork", "KSPI", *bank, "--answer", str(declared))
        check("объявленная граница — отказ команды с кодом 3",
              out.returncode == 3 and "corridor" in out.stdout,
              f"returncode={out.returncode}, out={out.stdout[:300]}")

        mixed = tmp / "mixed.json"
        mixed.write_text(json.dumps({"forks": [
            {"label": "bull", "thesis": "тезис", "channel": "прибыль",
             "channel_reason": "меняется прибыль", "horizon_months": 12,
             "must_be_true": ["у"],
             "corridor": [1.0, 2.0], "overrides": {}},
            {"label": "bear", "thesis": "тезис bear", "channel": "прибыль",
             "channel_reason": "меняется прибыль", "horizon_months": 12,
             "must_be_true": ["у"], "overrides": {
                 "eps_forward": {"value": 4800.0, "rationale": "ниже базы",
                                 "anchor_class": "company_guide",
                                 "confirms": "driver", "source": "гайд"},
                 "pe_low": {"value": 4.0, "rationale": "низ",
                            "anchor_class": "peer_stats", "confirms": "number",
                            "source": "сопоставимые"},
                 "pe_high": {"value": 6.0, "rationale": "верх",
                             "anchor_class": "peer_stats", "confirms": "number",
                             "source": "сопоставимые"}}}]},
            ensure_ascii=False), encoding="utf-8")
        out = run_cli("fork", "KSPI", *bank, "--answer", str(mixed))
        check("один отказавший форк не отменяет остальных",
              out.returncode == 0 and "не посчитаны" in out.stdout
              and "форк 'bull'" in out.stdout and "bear · «тезис bear»" in out.stdout,
              f"returncode={out.returncode}, out={out.stdout[:400]}")
        check("отказавший форк назван с причиной",
              "corridor" in out.stdout and "считает код" in out.stdout,
              out.stdout[:400])

        out = run_cli("fork", "KSPI", "--assumptions", str(assumptions),
                      "--answer", answer)
        check("банку нужен другой вход: предпосылки меры — не его контракт",
              out.returncode == 2 and "ddm_ri" in out.stderr,
              f"returncode={out.returncode}, stderr={out.stderr.strip()[-200:]}")

        out = run_cli("fork", "NOPE", *bank, "--answer", answer)
        check("нет в tickers.py — ошибка команды", out.returncode == 2,
              f"returncode={out.returncode}")


def _invest_state():
    """Состояние боевой БД: слой v2 не имеет права её менять."""
    path = ROOT / "invest.db"
    if not path.exists():
        return None
    st = path.stat()
    return st.st_size, st.st_mtime_ns


@patch("agent.baseline.prisms.gate", return_value=None)
def main(_prisms_gate) -> int:
    print("\nОфлайн-регрессия слоя 2 v2 — форк как ветка базовой линии "
          "(agent/fork.py)\n")
    before = _invest_state()
    with tempfile.TemporaryDirectory() as td:
        os.environ["AGENT_PROFILE_DB"] = str(Path(td) / "agent.db")
        try:
            section_parse()

            dell_base = baseline.build("DELL", assumptions=dict(DELL_ASSUMPTIONS))
            check("базовая линия DELL считается до форка",
                  not dell_base.is_refusal()
                  and dell_base.corridor == DELL_BASE_CORRIDOR,
                  f"{dell_base.errors if dell_base.is_refusal() else dell_base.corridor}")
            dell_basis = fork.basis(dell_base, price=DELL_PRICE,
                                    price_currency="USD")
            check("форк наследует коридор базовой линии",
                  dell_basis.corridor == DELL_BASE_CORRIDOR
                  and dell_basis.price == DELL_PRICE, f"{dell_basis!r}")

            kspi_base = baseline.build("KSPI", kspi_facts(), rate=RATE,
                                       growth=GROWTH, rate_why=RATE_WHY,
                                       growth_why=GROWTH_WHY)
            check("базовая линия банка считается до форка",
                  not kspi_base.is_refusal()
                  and kspi_base.corridor == KSPI_BASE_CORRIDOR,
                  "; ".join(m.message for m in kspi_base.mismatches))
            kspi_basis = fork.basis(kspi_base)
            check("объявленный дивиденд наследуется форком",
                  kspi_basis.dps == round(KSPI["dps_declared"] * FX, 2),
                  f"{kspi_basis.dps!r}")

            section_structural(dell_basis)
            section_channels(dell_basis, kspi_basis)
            section_anchors(dell_basis, kspi_basis)
            section_direction(dell_basis, kspi_basis)
            section_sotp()
            section_risk(kspi_basis)
            section_paid_in(kspi_basis)
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
    print(f"{GREEN}Все PASS{RESET} — форк считается тем же кодом, что и базовая "
          f"линия, а якорь, канал и направление проверяет структура.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
