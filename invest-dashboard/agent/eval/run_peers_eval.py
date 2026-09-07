#!/usr/bin/env python3
"""Офлайн-регрессия полосы мультипликатора из сопоставимых (SPC-008, agent/peers.py).

Полоса приходит из рынка, а не из головы модели. Сопоставимые берутся из
профиля, метрика — осмысленная для типа бизнеса: P/Bv при данном ROE для банка,
EV/Sales для аренды мощности, forward P/E для производителя.

Данные сопоставимых проверяются: если отчётность в одной валюте, а котировка
в другой, мультипликатор через них несопоставим — peer пропускается с
объяснением. Если чистых сопоставимых меньше двух, полоса не строится вовсе.

Эвал гоняется на фикстурах (agent/eval/fixtures/peers_*.json), без сети.

    python3 agent/eval/run_peers_eval.py   # exit 0, если все PASS
"""
import json
import os
import sys
from unittest.mock import patch
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import fork, peers, profile  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
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


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def section_band_from_clean_data():
    """Полоса строится при чистых данных: одна валюта, метрика доступна."""
    print("\n— полоса строится при чистых данных —")

    # DELL: forward_pe, HPE 12.81 / SMCI 6.96, DELL сам 20.37
    data = fixture("peers_dell.json")
    prof = profile.build("DELL")
    b = peers.build(prof, data)
    check("DELL: полоса построена", b is not None and b.band is not None,
          f"{b!r}")
    _eq("DELL: метрика forward_pe", b.kind, peers.FORWARD_PE)
    _eq("DELL: полоса из двух чистых сопоставимых",
        b.band, (6.96, 12.81))
    _eq("DELL: медиана", b.median, 12.81)
    _eq("DELL: чистых сопоставимых два", len(b.peers), 2)
    _eq("DELL: в чистых — HPE и SMCI", sorted(b.peers), ["HPE", "SMCI"])
    check("DELL: пропущенных нет", b.skipped == {}, f"{b.skipped!r}")
    check("DELL: собственная метрика доступна", b.own is not None
          and b.own["value"] == 20.37, f"{b.own!r}")
    check("DELL: позиция — выше полосы (20.37 > 12.81)",
          b.position is not None and "выше полосы" in b.position, f"{b.position!r}")

    # NBIS: ev_per_sales, IREN / CRWV
    data = fixture("peers_nbis.json")
    prof = profile.build("NBIS")
    b = peers.build(prof, data)
    check("NBIS: effective — core (неоклауд), comps IREN/CRWV",
          prof.effective.comps == ("IREN", "CRWV"), f"{prof.effective.comps!r}")
    check("NBIS: полоса построена через effective",
          b is not None and b.band is not None, f"{b!r}")
    _eq("NBIS: метрика ev_per_sales", b.kind, peers.EV_PER_SALES)
    _eq("NBIS: чистых сопоставимых два", len(b.peers), 2)
    check("NBIS: пропущенных нет", b.skipped == {}, f"{b.skipped!r}")
    # IREN: EV = 2.5B + 0.5B - 0.2B = 2.8B, rev = 450M → EV/S = 6.22
    # CRWV: EV = 18B + 3B - 1B = 20B, rev = 2.5B → EV/S = 8.0
    check("NBIS: полоса [6.22, 8.0]",
          abs(b.band[0] - 2.8e9 / 450e6) < 0.01
          and abs(b.band[1] - 20e9 / 2.5e9) < 0.01,
          f"{b.band!r}")
    # NBIS: EV = 35B + 2B - 0.5B = 36.5B, rev = 3B → EV/S = 12.17
    check("NBIS: позиция — выше полосы (12.17 > 8.0)",
          b.position is not None and "выше полосы" in b.position, f"{b.position!r}")


def section_currency_mismatch():
    """Peer с расходящимися валютами пропускается с причиной."""
    print("\n— peer с расходящимися валютами пропускается с причиной —")

    data = fixture("peers_kspi.json")
    prof = profile.build("KSPI")
    b = peers.build(prof, data)
    check("KSPI: полоса не построена — все 4 comps с разными валютами",
          b is not None and b.band is None, f"{b!r}")
    _eq("KSPI: чистых сопоставимых ноль", len(b.peers), 0)
    _eq("KSPI: пропущены все четыре", len(b.skipped), 4)
    check("KSPI: каждый пропущен с объяснением валюты",
          all("против" in reason for reason in b.skipped.values()),
          f"{b.skipped!r}")
    check("KSPI: ITUB пропущен — BRL против USD",
          "BRL" in b.skipped.get("ITUB", "") and "USD" in b.skipped.get("ITUB", ""),
          f"{b.skipped.get('ITUB')!r}")
    check("KSPI: HDB пропущен — INR против USD",
          "INR" in b.skipped.get("HDB", ""), f"{b.skipped.get('HDB')!r}")
    check("KSPI: GGAL пропущен — ARS против USD",
          "ARS" in b.skipped.get("GGAL", ""), f"{b.skipped.get('GGAL')!r}")
    check("KSPI: KB пропущен — KRW против USD",
          "KRW" in b.skipped.get("KB", ""), f"{b.skipped.get('KB')!r}")
    check("KSPI: собственная метрика тоже пропущена — KZT против USD",
          b.own is None, f"{b.own!r}")
    check("KSPI: причина отказа — меньше двух чистых",
          b.note is not None and "меньше двух" in b.note, f"{b.note!r}")


def section_fewer_than_two_clean():
    """При менее чем двух чистых сопоставимых полоса не строится."""
    print("\n— при менее чем двух чистых сопоставимых полоса не строится —")

    # DELL data, но у SMCI убираем forwardPE
    data = fixture("peers_dell.json")
    bad_smci = dict(data["SMCI"], forwardPE=None)
    sparse = {"HPE": data["HPE"], "SMCI": bad_smci, "DELL": data["DELL"]}
    prof = profile.build("DELL")
    b = peers.build(prof, sparse)
    check("один чистый peer — полоса не строится",
          b is not None and b.band is None, f"{b!r}")
    _eq("чистый один — HPE", len(b.peers), 1)
    check("SMCI в пропущенных с причиной",
          "SMCI" in b.skipped and "метрика недоступна" in b.skipped["SMCI"],
          f"{b.skipped!r}")
    check("причина отказа — меньше двух",
          b.note is not None and "меньше двух" in b.note, f"{b.note!r}")

    # Вообще без данных
    b = peers.build(prof, {})
    check("без данных — полоса не строится",
          b is not None and b.band is None, f"{b!r}")
    _eq("без данных — чистых ноль", len(b.peers), 0)
    _eq("без данных — оба в пропущенных", len(b.skipped), 2)
    check("без данных — причина «нет данных»",
          all("нет данных" in v for v in b.skipped.values()), f"{b.skipped!r}")


def section_position():
    """Позиция бумаги относительно полосы считается в обе стороны."""
    print("\n— позиция бумаги относительно полосы считается —")

    # DELL: own 20.37, band (6.96, 12.81) → выше
    data = fixture("peers_dell.json")
    prof = profile.build("DELL")
    b = peers.build(prof, data)
    check("DELL позиция — выше полосы",
          "выше полосы" in b.position, f"{b.position!r}")
    check("DELL процент превышения — (20.37-12.81)/12.81 ≈ 59%",
          "59%" in b.position, f"{b.position!r}")

    # Если бы own был внутри полосы
    inside = dict(data, DELL={**data["DELL"], "forwardPE": 10.0})
    b2 = peers.build(prof, inside)
    _eq("при own=10 при полосе 6.96–12.81 — внутри",
        b2.position, "внутри полосы")

    # Если бы own был ниже полосы
    below = dict(data, DELL={**data["DELL"], "forwardPE": 5.0})
    b3 = peers.build(prof, below)
    check("при own=5 при полосе 6.96–12.81 — ниже",
          "ниже полосы" in b3.position, f"{b3.position!r}")

    # Без own данных позиции нет
    no_own = {k: v for k, v in data.items() if k != "DELL"}
    b4 = peers.build(prof, no_own)
    check("без own данных — позиции нет", b4.position is None, f"{b4.position!r}")


def section_fork_peer_band():
    """Форк получает полосу классом «статистика сопоставимых»."""
    print("\n— форк получает полосу классом «статистика сопоставимых» —")

    data = fixture("peers_dell.json")
    prof = profile.build("DELL")
    band = peers.build(prof, data)

    # Ответ модели: pe_low и pe_high подпёрты peer_stats, значения — из band
    raw = json.dumps({"forks": [{
        "label": "bull",
        "thesis": "AI-серверный backlog конвертируется в выручку быстрее цикла",
        "channel": "прибыль",
        "channel_reason": "тезис меняет forward EPS",
        "horizon_months": 12,
        "must_be_true": ["backlog AI-сегмента не ниже 10 млрд USD"],
        "overrides": {
            "eps_forward": {
                "value": 19.0,
                "rationale": "прибыль на акцию вперёд растёт из-за AI-сегмента",
                "anchor_class": "company_guide",
                "confirms": "driver",
                "source": "гайд DELL: рост AI-портфеля"
            },
            "pe_low": {
                "value": band.band[0],
                "rationale": "нижняя граница полосы сопоставимых",
                "anchor_class": "peer_stats",
                "confirms": "number",
                "source": f"SMCI forward P/E {band.band[0]}"
            },
            "pe_high": {
                "value": band.band[1],
                "rationale": "верхняя граница полосы сопоставимых",
                "anchor_class": "peer_stats",
                "confirms": "number",
                "source": f"HPE forward P/E {band.band[1]}"
            }
        }
    }]}, ensure_ascii=False)

    # Базовая линия DELL
    from agent import baseline  # noqa: E402
    DELL_ASSUMPTIONS = {
        "fcf_base": 140.0, "shares": 10.0,
        "scenarios": {"base": {"growth_path": [0.10] * 5, "discount_rate": 0.10,
                               "terminal_growth": 0.02}},
    }
    dell_base = baseline.build("DELL", assumptions=dict(DELL_ASSUMPTIONS))
    dell_basis = fork.basis(dell_base, price=479.81, price_currency="USD")

    forks, problems = fork.parse(raw)
    check("форк разбирается без проблем формы", problems == (), f"{problems!r}")
    _eq("один форк", len(forks), 1)

    f = forks[0]
    _eq("метка bull", f.label, "bull")
    _eq("канал прибыль", f.channel, fork.EARNINGS)

    # Проверяем, что переопределения ссылаются на peer_stats
    check("pe_low — peer_stats",
          f.overrides["pe_low"].anchor_class == fork.PEER_STATS,
          f"{f.overrides['pe_low'].anchor_class!r}")
    check("pe_high — peer_stats",
          f.overrides["pe_high"].anchor_class == fork.PEER_STATS,
          f"{f.overrides['pe_high'].anchor_class!r}")
    check("значения pe_low/pe_high — из полосы сопоставимых",
          f.overrides["pe_low"].value == band.band[0]
          and f.overrides["pe_high"].value == band.band[1],
          f"pe_low={f.overrides['pe_low'].value}, pe_high={f.overrides['pe_high'].value}, "
          f"band={band.band}")

    # Форк считает коридор с переданной полосой
    res = fork.evaluate(dell_basis, f, peer_band=band)
    check("форк с peer_band посчитан", not res.is_refusal(), f"{res.refusals!r}")
    _eq("коридор из предпосылок: 19.0 × 6.96–12.81",
        res.corridor, (132.24, 243.39))
    check("peer_band передан в результат",
          res.peer_band is band, f"{res.peer_band!r}")
    check("peer_band в результате — та же полоса",
          res.peer_band.band == band.band and res.peer_band.kind == band.kind,
          f"{res.peer_band!r}")

    # Проверка направления: bull ниже базовой линии (середина 187.81 < 250.81)
    check("bull-форк с peer-полосой ниже базовой линии — замечание",
          len(res.warnings) == 1 and "ниже базовой линии" in res.warnings[0],
          f"{res.warnings!r}")
    check("замечание говорит о премии к сопоставимым",
          "премия" in res.warnings[0], res.warnings[0])

    # Форк без peer_band (по умолчанию None) — обратная совместимость
    res_no_band = fork.evaluate(dell_basis, f)
    check("форк без peer_band считается так же",
          not res_no_band.is_refusal()
          and res_no_band.corridor == res.corridor,
          f"{res_no_band.corridor!r}")
    check("без peer_band поле пусто",
          res_no_band.peer_band is None, f"{res_no_band.peer_band!r}")


def section_comps_warning():
    """SPC-009 §3.2: пустой comps предупреждает, а не молчит."""
    print("\n— пустой comps в профиле предупреждает —")
    import warnings as warnings_module
    with warnings_module.catch_warnings(record=True) as caught:
        warnings_module.simplefilter("always")
        prof = profile.build("PYPL")
    check("PYPL: comps пуст", prof.effective.comps == (), f"{prof.effective.comps!r}")
    check("PYPL: profile.build предупреждает про пустой comps",
          any("comps" in str(w.message) for w in caught), f"{[str(w.message) for w in caught]!r}")
    with warnings_module.catch_warnings(record=True) as caught:
        warnings_module.simplefilter("always")
        profile.build("DELL")
    check("DELL: comps не пуст — предупреждения нет",
          not any("comps" in str(w.message) for w in caught),
          f"{[str(w.message) for w in caught]!r}")


def section_fiscal_year_warning():
    """SPC-009 §3.5: fiscal_year_end расходится больше квартала — предупреждение."""
    print("\n— fiscal_year_end дальше квартала — предупреждение —")
    data = json.loads((FIXTURES / "peers_dell.json").read_text(encoding="utf-8"))
    data["DELL"]["fiscal_year_end"] = "2026-01-31"
    data["HPE"]["fiscal_year_end"] = "2025-08-31"   # 5 месяцев от января
    data["SMCI"]["fiscal_year_end"] = "2025-11-30"  # 2 месяца от января
    b = peers.build(profile.build("DELL"), data)
    check("предупреждение только про HPE (дальше квартала)",
          any("HPE" in w for w in b.warnings)
          and not any("SMCI" in w for w in b.warnings), f"{b.warnings!r}")

    data2 = json.loads((FIXTURES / "peers_dell.json").read_text(encoding="utf-8"))
    b2 = peers.build(profile.build("DELL"), data2)
    check("без fiscal_year_end в данных — предупреждений нет",
          b2.warnings == (), f"{b2.warnings!r}")


def section_contracts():
    print("\n— контракты слоя —")
    src = open(Path(ROOT / "agent" / "peers.py"), encoding="utf-8").read()
    check("peers.py не ходит в сеть",
          not any(w in src for w in ("yfinance", "requests", "urllib", "http")))
    check("метрики три, sum_of_parts — не метрика",
          peers.PBV_PER_ROE == "pbv_per_roe"
          and peers.EV_PER_SALES == "ev_per_sales"
          and peers.FORWARD_PE == "forward_pe"
          and peers.SUM_OF_PARTS == "sum_of_parts",
          f"pbv={peers.PBV_PER_ROE!r} ev={peers.EV_PER_SALES!r} "
          f"pe={peers.FORWARD_PE!r}")
    check("PeerBand — dataclass с полями band, peers, skipped, own, position",
          all(f in peers.PeerBand.__dataclass_fields__
              for f in ("kind", "band", "median", "peers", "skipped",
                        "own", "position", "note")),
          f"{list(peers.PeerBand.__dataclass_fields__)!r}")

    print("\n— без comps и sum_of_parts полоса не строится —")
    mu_band = peers.build(profile.build("MU"), {})
    check("MU: comps есть (WDC, STX), но без данных — полоса не строится",
          mu_band is not None and mu_band.band is None, f"{mu_band!r}")
    nbis_band = peers.build(profile.build("NBIS"), {})
    check("NBIS: effective — core с comps, без данных — полоса не строится",
          nbis_band is not None and nbis_band.band is None, f"{nbis_band!r}")
    check("generic нет comps → None",
          peers.build(profile.build("PYPL"), {}) is None)


def _invest_state():
    """Состояние боевой БД: слой v2 не имеет права её менять."""
    path = ROOT / "invest.db"
    if not path.exists():
        return None
    st = path.stat()
    return st.st_size, st.st_mtime_ns


@patch("agent.baseline.prisms.gate", return_value=None)
def main(_prisms_gate) -> int:
    print("\nОфлайн-регрессия полосы мультипликатора из сопоставимых "
          "(agent/peers.py)\n")
    before = _invest_state()
    with tempfile.TemporaryDirectory() as td:
        os.environ["AGENT_PROFILE_DB"] = str(Path(td) / "agent.db")
        try:
            section_band_from_clean_data()
            section_currency_mismatch()
            section_fewer_than_two_clean()
            section_position()
            section_fork_peer_band()
            section_comps_warning()
            section_fiscal_year_warning()
            section_contracts()
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
    print(f"{GREEN}Все PASS{RESET} — полоса мультипликатора приходит из рынка, "
          f"а не из головы модели.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
