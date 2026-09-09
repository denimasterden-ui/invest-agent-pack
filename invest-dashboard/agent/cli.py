#!/usr/bin/env python3
"""Команда слоёв v2 — точка входа (SPC-008, решение 19: команда на слой,
остановка на решении человека).

    python3 agent/cli.py profile NBIS
    python3 agent/cli.py set NBIS drivers '["развёрнутые МВт", "законтрактованные 5 ГВт"]' \
        --reason "NBIS раскрыл контракты в отчёте" --author material
    python3 agent/cli.py confirm NBIS 2
    python3 agent/cli.py history NBIS
    python3 agent/cli.py baseline KSPI --facts kspi.json --rate 0.17 --growth 0.09 \
        --rate-why "ставка ЦБ РК плюс премия за акционерный риск" \
        --growth-why "инфляция Казахстана плюс реальный рост экономики"
    python3 agent/cli.py ingest KSPI --material vyzhimka.txt --out answer.md
    python3 agent/cli.py research NBIS --out nbis-material.txt
    python3 agent/cli.py ingest KSPI --answer answer.md --facts kspi.json \
        --rate 0.17 --growth 0.09 --rate-why "ставка ЦБ РК" --growth-why "инфляция"
    python3 agent/cli.py fork KSPI --facts kspi.json --rate 0.17 --growth 0.09 \
        --rate-why "ставка ЦБ РК" --growth-why "инфляция" --answer forks.json
    python3 agent/cli.py fork DELL --assumptions dell.json --facts dell-facts.json \
        --answer forks.json

Команда показывает то, что знает слой, и заканчивается решением: предложенная
моделью смена меры видна как ожидающая подтверждения и вступает в силу только
после `confirm` — отдельной команды человека, по номеру конкретного события.
Коды выхода: 0 — расчёт состоялся, 2 — ошибка вызывающего (неизвестный тикер,
кривой файл фактов), 3 — отказ слоя: факты или разбор не сошлись, ничего не
записано, 4 — сбой вызова модели (D21): разбор не начинался.
"""
import argparse
import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.kernel import tickers  # noqa: E402
from agent.kernel import store_client  # noqa: E402
from agent import baseline, coherence, explain, fork, freshness, idea, ingest, measure_contract, peers, prisms, profile, profile_store, recheck, rules, scope  # noqa: E402


class CmdError(Exception):
    """Ошибка пользователя (не слоя): неизвестный тикер, поле, значение."""


def _resolve(label):
    entry = tickers.resolve(label)
    if entry is None:
        raise CmdError(f"{label}: нет в tickers.py — про эту бумагу слой "
                       f"ничего не знает")
    return entry


def _parse_value(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def cmd_profile(args, yf_module=None):
    entry = _resolve(args.ticker)
    p = profile.build(entry["key"])
    peer_data = (_collect_peer_data(p.ticker, yf_module=yf_module)
                 if p.effective.comps else {})
    rows = [
        ("бизнес", p.business_kind),
        ("мера", f"{p.measure} — {p.measure_reason}"),
        ("считается сейчас", "да" if p.measure_implemented
         else f"нет — мера {p.measure} ждёт слой базовой линии"),
    ]
    if p.core is not None:
        rows.append(("внутри", p.core.business_kind))
    e = p.effective
    rows += [
        ("драйверы", ", ".join(e.drivers)),
        ("потолки", ", ".join(e.caps)),
        ("сопоставимые", _with_metric(e, peer_data)),
        ("пробел данных", ", ".join(e.data_gaps) or "—"),
    ]
    width = max(len(k) for k, _ in rows)
    print(f"{p.ticker} — {entry['name']} [{p.research_type}]")
    for key, value in rows:
        print(f"  {key.ljust(width)}  {value}")

    # Введённые вручную драйверы — с происхождением каждого числа.
    df = p.driver_facts or {}
    if df:
        print(f"  {'драйверы вручную'.ljust(width)}  {len(df)} шт.")
        for name, fact in sorted(df.items()):
            print(f"    {name} = {fact['value']:g}  "
                  f"[{fact['anchor_class']}/{fact['confirms']}] "
                  f"{fact.get('source') or 'источника нет'} "
                  f"— «{fact.get('reason', '')}»")

    _print_pending(p.ticker)
    _print_history(p.ticker)
    return 0


def _with_metric(effective, peer_data=None):
    if not effective.comps:
        return f"— (сравнение в единицах {effective.multiple_metric})"
    band = peers.build(effective, peer_data)
    values = band.peers if band is not None else {}
    rendered = []
    for comp in effective.comps:
        name = comp["name"] if isinstance(comp, dict) else comp
        metric = values.get(name)
        rendered.append(f"{name} {metric['value']:g}" if metric
                        else f"{name} (н/д)")
    return (", ".join(rendered)
            + f" — в единицах {effective.multiple_metric}")


def _collect_peer_data(ticker, yf_module=None):
    """Load the existing hyphen-directory collector without duplicating it."""
    try:
        path = ROOT / "agent-run" / "collect_peers.py"
        spec = importlib.util.spec_from_file_location("agent_run_collect_peers", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.collect(ticker, yf_module=yf_module)
    except Exception:
        return {}


def _research_contract_hint(prof):
    """Use the peer collector's comp checklist without starting its network IO."""
    try:
        path = ROOT / "agent-run" / "collect_peers.py"
        spec = importlib.util.spec_from_file_location(
            "agent_run_collect_peers_contract", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.contract_coverage_hint(prof)
    except Exception:
        return ""


def _print_pending(ticker):
    _state, pending = profile_store.replay(ticker)
    if not pending:
        return
    decided = {r["field"]: r["id"]
               for r in profile_store.timeline(ticker) if r["confirmed"]}
    print("  предложения без решения:")
    for item in pending:
        superseded = item["id"] < decided.get(item["field"], -1)
        mark = " (перекрыто подтверждённым)" if superseded else ""
        print(f"    #{item['id']} {item['field']} → {_short(item['value'])}"
              f" — «{item['reason']}» ({item['author']}){mark}")
        if not superseded:
            print(f"      решение: python3 agent/cli.py confirm {ticker} "
                  f"{item['id']}")


def _print_history(ticker):
    events = profile_store.timeline(ticker)
    if not events:
        return
    print("  история:")
    for r in events:
        confirmed = (f"; подтвердил {r['confirmed_by']} {r['confirmed_at']}"
                     if r["confirmed_by"] else "")
        old = f"{_short(r['old_value'])} → " if r["old_value"] is not None else ""
        print(f"    {r['created_at']}  {r['field']}: {old}"
              f"{_short(r['new_value'])} — «{r['reason']}» "
              f"({r['author']}{confirmed})")


def _short(value):
    return value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False)


def cmd_set(args):
    entry = _resolve(args.ticker)
    p = profile.build(entry["key"])
    value = _parse_value(args.value)
    try:
        if store_client.configured():
            candidate_id = store_client.propose_profile_candidate(
                entry["key"], args.field, value, args.reason,
                "analyst_judgement", "", author=args.author)
            applied = False
        else:
            candidate_id = None
            applied = profile.record_change(
                entry["key"], args.field, value,
                reason=args.reason, author=args.author)
    except ValueError as e:
        raise CmdError(str(e)) from e
    if applied:
        print(f"{p.ticker}: {args.field} записано (подтверждения не требует)")
    else:
        event = candidate_id if candidate_id is not None else "<номер события>"
        print(f"{p.ticker}: {args.field} записано как предложенное — в силу "
              f"вступит после `confirm {p.ticker} {event}`")
    return 0


def cmd_driver(args):
    """Ввод драйвера вручную с указанием источника (SPC-008, тикет 7/11).

    Драйверы, которых нет в автоисточнике (data_gaps профиля), вводятся
    событием профиля с классом якоря, признаком подтверждения и источником —
    тем же механизмом, что и всё остальное знание. Число, названное компанией
    публично, получает высшую категорию подтверждённости.
    """
    entry = _resolve(args.ticker)
    ticker = entry["key"]

    # Контракт якоря — тот же, что у форков: analyst_judgement совместим
    # только с estimate, иначе утверждается несуществующий источник.
    if args.anchor_class not in fork.ANCHOR_CLASSES:
        raise CmdError(f"{args.anchor_class!r}: неизвестный класс якоря — "
                       f"{' | '.join(fork.ANCHOR_CLASSES)}")
    if args.confirms not in fork.CONFIRMS:
        raise CmdError(f"{args.confirms!r}: неизвестный confirms — "
                       f"{' | '.join(fork.CONFIRMS)}")
    if args.anchor_class == fork.ANALYST_JUDGEMENT and args.confirms == fork.NUMBER:
        raise CmdError(
            f"analyst_judgement + number: суждение аналитика не может "
            f"подтверждать само число — это утверждение несуществующего "
            f"источника; используйте estimate или другой класс якоря")
    if args.anchor_class != fork.ANALYST_JUDGEMENT and not (args.source or "").strip():
        raise CmdError(f"якорь {args.anchor_class} требует источник — чей это "
                       f"источник, а не откуда взято")

    p = profile.build(ticker)
    current = dict(p.driver_facts or {})
    current[args.driver_name] = {
        "value": args.value,
        "anchor_class": args.anchor_class,
        "source": args.source or "",
        "confirms": args.confirms,
        "reason": args.reason,
    }
    try:
        applied = profile.record_change(ticker, "driver_facts", current,
                                        reason=args.reason, author=args.author)
    except ValueError as e:
        raise CmdError(str(e)) from e
    if applied:
        print(f"{ticker}: драйвер {args.driver_name} = {args.value} записан "
              f"[{args.anchor_class}/{args.confirms}] {args.source or 'источника нет'} "
              f"— «{args.reason}»")
    else:
        print(f"{ticker}: драйвер {args.driver_name} записан как предложение — "
              f"в силу вступит после `confirm {ticker} <номер события>`")
    return 0


def cmd_confirm(args):
    ticker = _resolve(args.ticker)["key"]
    n = store_client.approve_profile_candidate(
        ticker, args.event_id, author=args.by
    )
    if not n:
        raise CmdError(f"{ticker}: предложения #{args.event_id} нет — номер "
                       f"смотри в выводе `profile`")
    print(f"{ticker}: событие #{args.event_id} подтверждено, решение записано")
    return 0


def cmd_history(args):
    ticker = _resolve(args.ticker)["key"]
    events = profile_store.timeline(ticker)
    if not events:
        print(f"{ticker}: событий нет — профиль пока только seed по типу бизнеса")
        return 0
    _print_history(ticker)
    return 0


def cmd_prisms(args):
    """Print factual stock signals and every analyst prism, without choosing."""
    entry = _resolve(args.ticker)
    prof = profile.build(entry["key"])
    facts = _load_json_object(args.facts, "фактов") if args.facts else {}
    profile_data = dataclasses.asdict(prof)
    profile_data["has_stakes"] = bool(entry.get("has_stakes"))
    stock_signals = prisms.signals(profile_data, facts)

    print(f"{prof.ticker} — {entry['name']} — призмы после сбора информации")
    print("  Сток-признаки (из фактов):")
    for name, value in stock_signals.items():
        rendered = "н/д" if value is None else json.dumps(
            value, ensure_ascii=False)
        print(f"    {name}: {rendered}")

    print("  Каталог призм:")
    for prism in prisms.catalog():
        print(f"    {prism.name} · признаки: {prism.signs} · "
              f"предлагает: {prism.offers}")
    print("  прогони призмы против признаков → выбери меру → set scope "
          "<measure+reason> → confirm")
    return 0


def cmd_drift(args):
    ticker = _resolve(args.ticker)["key"]
    rows = store_client.get_drift(ticker)
    if not rows:
        print(f"{ticker}: сохранённых базовых линий нет")
        return 0
    print(f"{ticker} — движение базового коридора")
    for row in rows:
        low, high = row["corridor"]
        if row["previous_corridor"] is None:
            movement = "первая точка"
        else:
            old_low, old_high = row["previous_corridor"]
            movement = (f"{old_low:g}–{old_high:g} → {low:g}–{high:g}  "
                        f"{row['drift']:+.1%}")
        print(f"  {row['run_at']}  {row['measure']}  {low:g}–{high:g}  {movement}")
    return 0


def _as_list(value):
    """MCP отдаёт одну строку словарём, а не списком из одного — сгладить."""
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    return list(value)


def _fmt_date(value):
    """ISO-таймстамп → YYYY-MM-DD; пусто — прочерк."""
    return value[:10] if value else "—"


def _print_roster():
    """Роестр канона: все бумаги, свежие сверху, с флагами активности."""
    where = "канон" if store_client.configured() else "локальный фолбэк"
    rows = store_client.list_tickers()
    if not rows:
        print(f"Роестр пуст ({where}) — канон недоступен или без профилей")
        return 0
    worked = [r for r in rows if r.get("baseline_count") or r.get("fork_count")
              or r.get("material_count")]
    seed_only = len(rows) - len(worked)
    print(f"Канон — {len(rows)} бумаг, из них {len(worked)} с прогонами ({where})")
    print(f"  {'тикер':<9} {'мера':<8} {'верс':<4} {'обновлён':<11} что накоплено")
    for r in worked:
        parts = []
        if r.get("baseline_count"):
            parts.append(f"baseline·{_fmt_date(r.get('last_baseline'))}")
        if r.get("fork_count"):
            parts.append(f"форки×{r['fork_count']}·{_fmt_date(r.get('last_fork'))}")
        if r.get("material_count"):
            parts.append(f"материал×{r['material_count']}")
        print(f"  {r['ticker']:<9} {r.get('measure','?'):<8} "
              f"v{str(r.get('version','?')):<3} {_fmt_date(r.get('updated_at')):<11} "
              f"{' · '.join(parts)}")
    if seed_only:
        names = ", ".join(r["ticker"] for r in rows if r not in worked)
        print(f"\n  + {seed_only} только профиль (seed, без прогонов): {names}")
    print("\n  провалиться внутрь: cli show ТИКЕР")
    return 0


def cmd_show(args):
    """Инвентарь канона: без тикера — роестр всех бумаг, с тикером — провал внутрь.

    Аналог `ls runs/ + README` для файловой раскладки v1 — но из центрального
    хранилища: список бумаг с датами обновления, а по бумаге — профиль,
    последняя базовая линия, активные форки, материалы и точки дрейфа.
    """
    if not args.ticker:
        return _print_roster()
    entry = _resolve(args.ticker)
    ticker = entry["key"]
    where = "канон" if store_client.configured() else "локальный фолбэк"
    print(f"{ticker} — {entry['name']} — инвентарь ({where})")

    prof = store_client.get_profile(ticker)
    if prof:
        comps = ", ".join(
            comp.get("name", "?") if isinstance(comp, dict) else str(comp)
            for comp in (prof.get("comps") or [])
        ) or "—"
        gaps = ", ".join(prof.get("data_gaps") or []) or "—"
        print(f"  профиль     мера {prof.get('measure','?')} · v{prof.get('version','?')}"
              f" · обновлён {prof.get('updated_at','?')}")
        print(f"              драйверы: {', '.join(prof.get('drivers') or []) or '—'}")
        print(f"              сопоставимые: {comps}")
        print(f"              пробелы данных: {gaps}")
    else:
        print("  профиль     — (нет)")

    explain_mode = getattr(args, "explain", False)
    detail = getattr(args, "detail", False) or explain_mode
    # Детализация — тяжёлый read (get_scenario_detail); обычный show — лаконичный
    # get_scenarios (контракт #92: тяжёлые поля не текут в лёгкий read).
    scen = (store_client.get_scenario_detail(ticker) if detail
            else store_client.get_scenarios(ticker))
    base = scen.get("baseline")
    if base:
        low, high = base["corridor"]
        print(f"  baseline    {low:g}–{high:g} · мера {base['measure']}"
              f" · {base['run_at']}")
        if detail:
            for line in explain.baseline_lines(
                    base["measure"], base.get("assumptions"), base["corridor"]):
                print(f"                {line}")
    else:
        print("  baseline    — (нет)")
    forks = scen.get("forks") or []
    if forks:
        for f in forks:
            low, high = f["corridor"]
            ch = f.get("channel") or "—"
            print(f"  форк {f['label']:<6} {low:g}–{high:g} · канал {ch} · {f['run_at']}")
            if detail:
                if f.get("thesis"):
                    print(f"                тезис: {f['thesis']}")
                for cond in (f.get("must_be_true") or []):
                    print(f"                · условие: {cond}")
                for name, ov in (f.get("overrides") or {}).items():
                    if isinstance(ov, dict):
                        src = ov.get("source") or "источника нет"
                        print(f"                · {name} = {_short(ov.get('value'))}"
                              f"  [{ov.get('anchor_class','?')}/{ov.get('confirms','?')}]"
                              f" {src} — «{ov.get('rationale','')}»")
    else:
        print("  форки       — (нет)")

    mats = _as_list(store_client.get_material(ticker))
    if mats:
        for m in mats:
            author = m.get("author") or "—"
            print(f"  материал    {m.get('source_name','?')}"
                  f" · автор {author} · {m.get('created_at','?')}")
            if m.get("source_link"):
                print(f"                источник: {m['source_link']}")
            if detail and m.get("synthesis"):
                print("                ─── синтез ───")
                for line in m["synthesis"].splitlines():
                    print(f"                {line}")
    else:
        print("  материал    — (нет)")

    drift = _as_list(store_client.get_drift(ticker))
    if drift:
        last = drift[-1]
        d = last.get("drift")
        tail = f"последний дрейф {d:+.1%}" if d is not None else "одна точка"
        print(f"  дрейф       {len(drift)} точек · {tail}")
    else:
        print("  дрейф       — (нет)")

    if explain_mode:
        print("\n" + "=" * 70)
        print("АНАЛИТИК (Claude Code): напиши инвест-вывод для человека по данным")
        print("выше. Метод — три оси: (1) цена vs baseline (сверь с текущей ценой),")
        print("(2) дрейф baseline (сместилась ли справедливая оценка), (3) тезис жив")
        print("или сломан (материал + заякоренность форков: company_guide/own_history")
        print("подпёрты, analyst_judgement = наблюдение). Отдели нарративную просадку")
        print("от слома тезиса. НЕ выдумывай числа сверх канона. Затем причеши через")
        print("/humanizer — вывод показывается человеку, в канон НЕ пишется.")
        print("=" * 70)
    return 0


def cmd_add_material(args):
    """Внести разбор материала (синтез) в канон — шаг «свежий отчёт» скилла.

    Аналитик прочитал источник, написал выжимку → она ложится в канон и
    закрывает гейт свежести (материал не старше отчёта пропускает оценку).
    """
    entry = _resolve(args.ticker)
    ticker = entry["key"]
    synthesis = _read_text(args.synthesis)
    source_name = args.source_name or Path(args.synthesis).stem
    for_measure = (args.for_measure if args.for_measure is not None
                   else profile.build(ticker).measure)
    res = store_client.add_material(
        ticker, source_name=source_name, source_link=args.link or "",
        synthesis=synthesis, author=args.author, for_measure=for_measure)
    if res is None and not store_client.configured():
        raise CmdError("канон не настроен (нет INVEST_MCP_URL) — материал "
                       "хранится только на сервере, локального фолбэка нет")
    print(f"{ticker}: материал «{source_name}» внесён в канон"
          f"{f' (id {res})' if res else ''}")
    return 0


_IMG_SUFFIX = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
               "image/webp": ".webp", "image/bmp": ".bmp", "image/svg+xml": ".svg"}


def cmd_images(args):
    """Выгрузить картинки-кандидаты из .mhtml в каталог (для chart-субагента).

    Отбор «статейных» картинок — knowledge._mhtml_image_candidates (аватары/
    трекеры/реклама отсеяны, #43). Печатает манифест: индекс, тип, путь.
    """
    import knowledge
    raw = Path(args.mhtml).read_bytes()
    candidates = [item for item in knowledge._mhtml_image_candidates(raw) if item.content]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for index, item in enumerate(candidates):
        suffix = _IMG_SUFFIX.get((item.content_type or "").split(";")[0].strip().lower(), ".png")
        path = out / f"img_{index:02d}{suffix}"
        path.write_bytes(item.content)
        print(f"{index:02d}\t{item.content_type}\t{path}")
    if not candidates:
        print("картинок-кандидатов нет", file=sys.stderr)
    return 0


def cmd_baseline(args):
    """Слой 1: базовая линия мерой из профиля.

    Банк получает факты отчётности, остальные меры — объявленные допущения.
    Слой сам в сеть не ходит. Отказ — исход, а не падение: код 3.
    """
    entry = _resolve(args.ticker)
    ticker = entry["key"]
    prof = profile.build(ticker)
    measure = prof.measure
    if args.assumptions:
        if any(value is not None for value in
               (args.rate, args.growth, args.rate_why, args.growth_why)):
            raise CmdError("с --assumptions факты нужны только для проверки "
                           "свежести; --rate/--growth относятся к банку")
        if measure == profile.DDM_RI:
            raise CmdError(f"{ticker}: мера {measure} считается на фактах "
                           "отчётности с объявленными ставкой и ростом — "
                           "--assumptions ей не вход")
        refusal = scope.gate(prof)
        if refusal:
            _print_cli_scope_refusal(ticker, entry, measure, refusal)
            return 3
        refusal = _measure_contract_refusal(prof, ticker)
        if refusal:
            _print_cli_measure_contract_refusal(
                ticker, entry, measure, refusal)
            return 3
        refusal = _prisms_refusal(prof, ticker, args.facts is not None)
        if refusal:
            _print_cli_prisms_refusal(ticker, entry, measure, refusal)
            return 3
        refusal = _assumptions_freshness_refusal(args, ticker)
        if refusal:
            _print_cli_freshness_refusal(ticker, entry, measure, refusal)
            return 3
        res = baseline.build(ticker, assumptions=_load_json(args.assumptions),
                             _has_info=True)
        facts = None
    else:
        if args.facts is None:
            raise CmdError("передайте --facts с --rate и --growth (банк) либо "
                           "--assumptions (мера из профиля)")
        if measure != profile.DDM_RI:
            raise CmdError(f"{ticker}: мера {measure} считается на объявленных "
                           "предпосылках — передайте --assumptions")
        missing = [name for name, value in
                   (("rate", args.rate), ("growth", args.growth),
                    ("rate-why", args.rate_why),
                    ("growth-why", args.growth_why)) if value is None]
        if missing:
            raise CmdError(f"факты есть, допущений нет: {', '.join(missing)}")
        facts = _load_facts(args.facts)
        res = baseline.build(ticker, facts, rate=args.rate, growth=args.growth,
                             rate_why=args.rate_why,
                             growth_why=args.growth_why)
    if res.is_refusal():
        _print_refusal(res, entry)
        return 3
    if measure == profile.DDM_RI:
        _print_baseline(res, entry, facts)
    else:
        _print_measure_baseline(res, entry)
    return 0


def _load_facts(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise CmdError(f"{path}: файл фактов не читается ({e})") from e
    except ValueError as e:
        raise CmdError(f"{path}: не JSON ({e})") from e
    try:
        return coherence.Facts.from_dict(raw)
    except ValueError as e:
        raise CmdError(str(e)) from e


def _print_baseline(res, entry, facts):
    d = res.detail
    low, high = res.corridor
    rows = [
        ("мера", f"{res.measure} — {res.measure_reason}"),
        ("цена", f"{res.price:.2f} {res.price_currency}"),
        ("коридор", f"{low:.2f} – {high:.2f} {res.price_currency}   "
                    f"две меры, расхождение {res.divergence:.1%} — "
                    f"{'сходятся, оценке можно верить' if res.converged else 'расходятся'}"),
        ("вердикт", res.verdict),
        ("мера 1: DDM", f"{d.ddm:.2f} — дивиденд {d.dps:.2f} объявлен "
                        f"вперёд при r − g = {(d.r - d.g) * 100:.0f} п.п."),
        ("мера 2: P/Bv", f"{d.pbv:.2f} — ROE {d.roe:.1%} → справедливый "
                         f"{d.pbv_multiple:.2f}x × капитал "
                         f"{d.book_value_per_share:.2f} на акцию"),
        ("допущения", f"r = {d.r:.0%} ({d.r_why}); "
                      f"g = {d.g:.0%} ({d.g_why}); "
                      f"курс {d.fx:.6g} {d.financial_currency}→"
                      f"{d.price_currency}"),
        ("факты", f"{facts.source or 'источник не назван'}; период до "
                  f"{facts.period_end}, в источнике доступно до "
                  f"{facts.available_end}"),
    ]
    if d.dps_trailing:
        rows.append(("не перепутать", f"выплаченный за прошлые 12 месяцев "
                    f"{d.dps_trailing:.2f} — не он в расчёте"))
    width = max(len(k) for k, _ in rows)
    print(f"{res.ticker} — {entry['name']} — базовая линия")
    for key, value in rows:
        print(f"  {key.ljust(width)}  {value}")


def _print_measure_baseline(res, entry):
    """Коридор небанковской меры; его границы уже рассчитаны measures."""
    low, high = res.corridor
    print(f"{res.ticker} — {entry['name']} — базовая линия")
    print(f"  мера      {res.measure} — {res.measure_reason}")
    print(f"  коридор   {low:.2f} – {high:.2f}")
    for warning in res.warnings:
        print(f"  внимание  {warning.code}: {warning.message}")


def _print_refusal(res, entry):
    print(f"{res.ticker} — {entry['name']} [{res.measure}] — базовая линия")
    print("  ОТКАЗ: расчёт остановлен до числа — данные не сошлись")
    for m in getattr(res, "mismatches", ()):
        print(f"    · {m.code}: {m.message}")
        print(f"      → {m.request}")
    for e in getattr(res, "errors", ()):
        # Слой мер отдаёт отказ строкой ошибки, а не рассогласованием фактов.
        print(f"    · {e}")


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise CmdError(f"{path}: замороженный ответ не читается ({e})") from e


def cmd_ingest(args):
    """Разбор материала по трём адресатам (SPC-008). Структурный split готовит
    аналитик (Claude) в JSON и подаёт через --answer; код валидирует форму
    (ingest.parse), сверяет числа с базовой линией (ingest.compare) и применяет
    (ingest.apply). Авто-вызова модели нет — аналитик и есть модель.

    --facts с объявленными допущениями включает сверку чисел материала с
    базовой линией; без неё вердикты остаются как сказано.
    """
    entry = _resolve(args.ticker)
    ticker = entry["key"]
    prof = profile.build(ticker)
    measure = prof.measure
    if getattr(args, "assumptions", None):
        if measure == profile.DDM_RI:
            raise CmdError(f"{ticker}: мера {measure} не принимает --assumptions")
        refusal = scope.gate(prof)
        if refusal:
            _print_cli_scope_refusal(ticker, entry, measure, refusal)
            return 3
        refusal = _assumptions_freshness_refusal(args, ticker)
        if refusal:
            _print_cli_freshness_refusal(ticker, entry, measure, refusal)
            return 3
    material = _material(args, ticker)
    base = _baseline_for_checks(args, ticker)
    answer = _read_text(args.answer)
    note = f"разбор из {Path(args.answer).name}, вызова не было"
    split = ingest.parse(answer, ticker, material.name)
    if base is not None:
        split = ingest.compare(split, base)
    if split.is_refusal():
        print(f"{ticker} — {material.name}")
        print("  ОТКАЗ: разбор не проходит по форме — ничего не записано")
        for p in split.problems:
            print(f"    · {p.code}: {p.message}")
        return 3
    written = ingest.apply(ticker, split)
    _print_split(split, entry, base, note, written)
    return 0


def cmd_research(args):
    """Печатает адресный ресёрч-бриф по незаполненным data_gaps профиля —
    задание для субагента-ресёрчера (изолированный контекст). LLM не вызывается:
    ресёрч выполняет субагент/аналитик, результат вносится через add-material,
    затем раскладывается ingest --answer. Общая методология — knowledge/
    research_method.md; здесь — только адресные пробелы этой бумаги.
    """
    entry = _resolve(args.ticker)
    prof = profile.build(entry["key"])
    try:
        print(ingest.build_research_prompt(prof))
    except ValueError as e:
        raise CmdError(str(e)) from e
    return 0


def _material(args, ticker):
    """Материал: файл, синтезы канона или старая выжимка из локальной БД."""
    if args.material:
        try:
            material = ingest.material_from_file(args.material)
        except ValueError as e:
            raise CmdError(str(e)) from e
        return (dataclasses.replace(material, name=args.source_name)
                if args.source_name else material)
    canonical = store_client.get_material(ticker)
    parsed = [
        (str(row.get("source_name", "")).strip(), synthesis.strip())
        for row in canonical
        if isinstance(row, dict)
        for synthesis in [row.get("synthesis")]
        if isinstance(synthesis, str) and synthesis.strip()
    ]
    if parsed:
        names = [name for name, _synthesis in parsed if name]
        return ingest.Material(name=", ".join(names) or f"{ticker}: канон",
                               text="\n\n".join(s for _name, s in parsed))
    material = ingest.latest_material(ticker)
    if material is None:
        raise CmdError(f"{ticker}: материала нет — ни --material, ни синтеза "
                       f"в каноне, ни выжимки в source_digests: разбирать нечего")
    return material


def _baseline_for_checks(args, ticker):
    """База для сверки чисел материала. Без фактов её нет — и это не ошибка."""
    measure = profile.build(ticker).measure
    if getattr(args, "assumptions", None):
        if measure == profile.DDM_RI:
            raise CmdError(f"{ticker}: мера {measure} не принимает --assumptions")
        if any(val is not None for val in
               (args.rate, args.growth, args.rate_why, args.growth_why)):
            raise CmdError("с --assumptions нельзя задавать банковские "
                           "--rate/--growth")
        return baseline.build(ticker, assumptions=_load_json(args.assumptions),
                              persist=False)
    if args.facts is None:
        return None
    if measure != profile.DDM_RI:
        raise CmdError(f"{ticker}: для сверки мерой {measure} передайте "
                       "--assumptions вместе с --facts")
    missing = [name for name, val in (("rate", args.rate), ("growth", args.growth),
                                      ("rate-why", args.rate_why),
                                      ("growth-why", args.growth_why))
               if val is None]
    if missing:
        raise CmdError(f"факты есть, допущений нет: {', '.join(missing)} — "
                       f"сверять материал не с чем")
    return baseline.build(ticker, _load_facts(args.facts),
                          rate=args.rate, growth=args.growth,
                          rate_why=args.rate_why, growth_why=args.growth_why,
                          persist=False)


def _print_split(split, entry, base, note, written):
    applied = {field: ok for field, ok in written}
    print(f"{split.ticker} — {entry['name']} — разбор материала")
    print(f"  {split.material_name}")
    print(f"  {note}")
    for part, label, items in (
            ("profile_updates", "Слой 0 — чем материал дополняет профиль",
             split.profile_updates),
            ("baseline_checks", "Слой 1 — чем материал проверяет базовую линию",
             split.baseline_checks),
            ("thesis_candidates", "Слой 2 — кандидаты тезисов",
             split.thesis_candidates)):
        print(f"\n  {label}" + ("" if items else " — материал ничего не принёс"))
        for item in items:
            for line in _print_item(part, item, applied, base):
                print(f"    {line}")
    if base is not None and base.is_refusal():
        print("\n  сверка чисел не состоялась: базовая линия на этих фактах "
              "отказала")
        for m in base.mismatches:
            print(f"    · {m.code}: {m.message}")


def _print_item(part, item, applied, base):
    if part == "profile_updates":
        state = "в силе" if applied.get(item.field) else "ждёт подтверждения"
        return [f"{item.field} → {_short(item.value)}   [{item.anchor_class}] "
                f"{item.source} — «{item.reason}» ({state})",
                f"число пришло через материал: {item.via}"]
    if part == "baseline_checks":
        mark = {"подтверждает": "✓", "противоречит": "✗"}.get(item.verdict, "~")
        against = (f"{ingest.format_number(item.value_financial)} против "
                   f"допущения {ingest.format_number(item.baseline_value)} — "
                   f"расхождение {item.delta:.0%}" if item.delta is not None
                   else f"{item.value:g} {item.unit} — сверка не состоялась")
        return [f"{mark} {item.what or item.key}: {against} — {item.verdict}",
                f"[{item.anchor_class}] {item.source}"
                + (f" · «{item.note}»" if item.note else "")]
    conditions = ", ".join(f"«{c}»" for c in item.must_be_true)
    return [f"{item.direction}  {item.thesis}",
            f"условия: {conditions}"
            + (f" · драйверы: {', '.join(item.drivers_touched)}"
               if item.drivers_touched else "")]


def cmd_fork(args):
    """Слой 2: тезис о будущем как ветка базовой линии.

    Базовая линия собирается здесь же из тех же входов, что и командой
    `baseline`: форк ветвит её предпосылки, а не снимок из истории. Ответ
    модели — замороженный файл: границы стоимости в нём искать нечего, их
    считает код. Отказ одного форка не отменяет остальных: у каждого своя
    структура, и читать можно и тот, что остался наблюдением.
    """
    entry = _resolve(args.ticker)
    ticker = entry["key"]
    prof = profile.build(ticker)
    measure = prof.measure
    if args.assumptions and measure != profile.DDM_RI:
        refusal = scope.gate(prof)
        if refusal:
            _print_cli_scope_refusal(ticker, entry, measure, refusal)
            return 3
        refusal = _measure_contract_refusal(prof, ticker)
        if refusal:
            _print_cli_measure_contract_refusal(
                ticker, entry, measure, refusal)
            return 3
        refusal = _prisms_refusal(prof, ticker, args.facts is not None)
        if refusal:
            _print_cli_prisms_refusal(ticker, entry, measure, refusal)
            return 3
        refusal = _assumptions_freshness_refusal(args, ticker)
        if refusal:
            _print_cli_freshness_refusal(ticker, entry, measure, refusal)
            return 3
    base = _baseline_for_fork(args, ticker)
    if base.is_refusal():
        _print_refusal(base, entry)
        return 3
    answer_text = _read_text(args.answer)
    forks, problems = fork.parse(answer_text)
    if not forks and problems:
        print(f"{ticker} — {entry['name']} — форки")
        print("  ОТКАЗ: ответ модели не проходит по форме — ничего не посчитано")
        for p in problems:
            print(f"    · {p.code}: {p.message}")
        return 3
    try:
        b = fork.basis(base, price=args.price, price_currency=args.quote_currency)
    except ValueError as e:
        raise CmdError(str(e)) from e
    print(f"{ticker} — {entry['name']} — форки базовой линии "
          f"{b.corridor[0]:,.2f}–{b.corridor[1]:,.2f} (мера {b.measure}) "
          f"при цене {b.price:,.2f} {b.price_currency}")
    if problems:
        print("  ответ модели частично не проходит по форме — эти форки "
              "не посчитаны:")
        for p in problems:
            print(f"    · {p.code}: {p.message}")
    computed = 0
    for item in forks:
        computed += _print_fork(b, item)
    if not computed:
        print("\n  ни один форк не посчитан")
        return 3
    _print_generation_recheck(answer_text, forks, b)
    return 0


def _print_generation_recheck(answer_text, forks, basis):
    """Recheck форков на момент генерации (SPC-009): условия must_be_true
    сверяются с фактами сразу при создании — не отстаёт ли условие уже сейчас.

    recheck-поле в fork-answer необязательно: без него сверять нечего и слой
    молчит. Печатаются только отставшие условия (статус «не сработало»).
    """
    outcomes, _problems = recheck.at_generation(answer_text, forks, basis)
    stale = [(o.fork_label, c) for o in outcomes for c in o.conditions
             if c.status == recheck.NOT_TRIGGERED]
    if not stale:
        return
    print("\n  recheck на момент генерации — условия отстают от фактов:")
    for label, c in stale:
        print(f"    · {label}: ОТСТАЁТ «{c.condition}» ↔ {c.fact}")


def _baseline_for_fork(args, ticker):
    """Базовая линия, которую ветвит форк: один вход, и тот по мере бумаги.

    Банк считается на фактах отчётности с объявленными ставкой и ростом,
    остальные меры — на объявленных предпосылках: это тот же контракт, что у
    команды `baseline`. Меру называет профиль, поэтому вход сверяется с ним
    здесь — слой базовой линии на чужом входе не отвечает.
    """
    measure = profile.build(ticker).measure
    if args.assumptions:
        if any(value is not None for value in
               (args.rate, args.growth, args.rate_why, args.growth_why)):
            raise CmdError("с --assumptions факты нужны только для проверки "
                           "свежести; --rate/--growth относятся к банку")
        if measure == profile.DDM_RI:
            raise CmdError(f"{ticker}: мера {measure} считается на фактах "
                           f"отчётности с объявленными ставкой и ростом — "
                           f"--assumptions ей не вход")
        return baseline.build(ticker, assumptions=_load_json(args.assumptions),
                              persist=False, _has_info=True)
    if args.facts is None:
        raise CmdError("базовой линии нет: передайте --facts с --rate и "
                       "--growth (банк) либо --assumptions (мера из профиля)")
    if measure != profile.DDM_RI:
        raise CmdError(f"{ticker}: мера {measure} считается на объявленных "
                       f"предпосылках — передайте --assumptions, а не факты с "
                       f"--rate и --growth")
    missing = [name for name, val in (("rate", args.rate), ("growth", args.growth),
                                      ("rate-why", args.rate_why),
                                      ("growth-why", args.growth_why))
               if val is None]
    if missing:
        raise CmdError(f"факты есть, допущений нет: {', '.join(missing)} — "
                       f"форк не на чем ветвить")
    return baseline.build(ticker, _load_facts(args.facts), rate=args.rate,
                          growth=args.growth, rate_why=args.rate_why,
                          growth_why=args.growth_why, persist=False)


def _load_json(path):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise CmdError(f"{path}: файл предпосылок не читается ({e})") from e
    except ValueError as e:
        raise CmdError(f"{path}: не JSON ({e})") from e
    if not isinstance(raw, dict):
        raise CmdError(f"{path}: предпосылки — JSON-объект")
    return raw


def _assumptions_freshness_refusal(args, ticker):
    """Gate assumptions measures using only price_context from --facts."""
    price_context = {}
    if args.facts is not None:
        raw = _load_json_object(args.facts, "фактов")
        value = raw.get("price_context")
        price_context = value if isinstance(value, dict) else {}
    return freshness.gate(price_context, ticker, store_client.get_material)


def _measure_contract_refusal(prof, ticker):
    """Gate the selected measure against canonical research materials."""
    materials = store_client.materials_for_contract(ticker, prof.measure)
    return measure_contract.gate(vars(prof), materials)


def _prisms_refusal(prof, ticker, facts_supplied):
    """Gate the selected measure on collected information and rationale."""
    has_info = facts_supplied or bool(store_client.get_material(ticker))
    return prisms.gate(vars(prof), has_info)


def _load_json_object(path, label):
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        raise CmdError(f"{path}: файл {label} не читается ({e})") from e
    except ValueError as e:
        raise CmdError(f"{path}: не JSON ({e})") from e
    if not isinstance(raw, dict):
        raise CmdError(f"{path}: {label} — JSON-объект")
    return raw


def _print_cli_freshness_refusal(ticker, entry, measure, message):
    print(f"{ticker} — {entry['name']} [{measure}] — базовая линия")
    print("  ОТКАЗ: расчёт остановлен до числа — данные не сошлись")
    print(f"    · freshness: {message}")
    request = ("собери collect_facts" if "не проверить" in message else
               "сначала ingest свежего отчёта, потом оценка")
    print(f"      → {request}")


def _print_cli_scope_refusal(ticker, entry, measure, message):
    print(f"{ticker} — {entry['name']} [{measure}] — базовая линия")
    print("  ОТКАЗ: расчёт остановлен до числа — охват не подтверждён")
    print(f"    · scope: {message}")
    print("      → set scope с мерой и обоснованием → confirm")


def _print_cli_measure_contract_refusal(ticker, entry, measure, message):
    print(f"{ticker} — {entry['name']} [{measure}] — базовая линия")
    print("  ОТКАЗ: расчёт остановлен до числа — ресёрч не покрывает меру")
    print(f"    · measure_contract: {message}")
    print(f"      → закройте недостающие узлы ресёрча для меры {measure}")


def _print_cli_prisms_refusal(ticker, entry, measure, message):
    print(f"{ticker} — {entry['name']} [{measure}] — базовая линия")
    print("  ОТКАЗ: расчёт остановлен до числа — мера не обоснована")
    print(f"    · prisms: {message}")
    print("      → соберите факты/материал и обоснуйте меру через "
          "cli prisms → set scope → confirm")


def _print_fork(b, item):
    """Один форк. Возвращает 1, если коридор посчитан."""
    res = fork.evaluate(b, item)
    f = res.fork
    if res.is_refusal():
        print(f"\n  ОТКАЗ · {f.label} — «{f.thesis}»")
        for r in res.refusals:
            print(f"    · {r}")
        return 0
    low, high = res.corridor
    years = res.expected["years"]
    paid = fork.paid_in(b.price, b.corridor, res.corridor)
    print(f"\n  {f.label} · «{f.thesis}»")
    print(f"    канал: {f.channel} — «{f.channel_reason}»")
    point = " (у канала дивиденд это уровень, а не полоса)" if low == high else ""
    print(f"    коридор {low:,.2f} – {high:,.2f} {b.price_currency}{point}")
    print(f"    условия: {', '.join('«%s»' % c for c in f.must_be_true)}")
    for name, ov in f.overrides.items():
        print(f"      {name} = {ov.value}   [{ov.anchor_class}/{ov.confirms}] "
              f"{ov.source or 'источника нет'} — «{ov.rationale}»")
    if res.derived:
        print(f"    вывел аналитик из подтверждённого драйвера: "
              f"{', '.join(res.derived)}")
    if res.estimated:
        print(f"    границы оценочные (источника нет): "
              f"{', '.join(res.estimated)} — коридор шире, чем доказан")
    for line in res.warnings:
        print(f"    ПРЕДУПРЕЖДЕНИЕ: {line}")
    print(f"    {paid.label}   (цена {b.price:,.2f} {b.price_currency})")
    e = res.expected
    print(f"    за {f.horizon_months} мес ({years:g} г.) к коридору: "
          f"низ {e['low'][1]:+.0%} · середина {e['mid'][1]:+.0%} · "
          f"верх {e['high'][1]:+.0%} годовых")
    if f.deferred_from is not None:
        print(f"    срок перенесён с {f.deferred_from} мес — коридор тот же, "
              f"годовая доходность ниже")
    print(f"    {len(f.must_be_true)} услов. · "
          f"{'ставка: несущие величины подпёрты' if res.bettable else 'НАБЛЮДЕНИЕ: несущая величина тезиса не подпёрта'}")
    return 1


def cmd_idea(args):
    """Layer 3: make a trade idea only from an explicitly selected fork."""
    entry = _resolve(args.ticker)
    ticker = entry["key"]
    base = _baseline_for_fork(args, ticker)
    if base.is_refusal():
        _print_refusal(base, entry)
        return 3
    forks, problems = fork.parse(_read_text(args.fork_answer))
    selected = next((item for item in forks if item.label == args.fork), None)
    if selected is None:
        print(f"{ticker} — {entry['name']} — идея")
        print(f"  ОТКАЗ: активный форк '{args.fork}' не найден — идея не создана")
        for problem in problems:
            print(f"    · {problem.code}: {problem.message}")
        return 3
    try:
        b = fork.basis(base, price=args.price,
                       price_currency=args.quote_currency)
        made = idea.build(
            b, selected, entry=args.entry, horizon_months=args.horizon_months,
            exit=args.exit, catalysts=tuple(args.catalyst), status=args.status)
        returns = idea.expected_return(made, b, current_price=b.price)
    except ValueError as exc:
        print(f"{ticker} — {entry['name']} — идея")
        print(f"  ОТКАЗ: {exc} — идея не создана")
        return 3
    print(f"{ticker} — {entry['name']} — идея на форк {made.fork.label} "
          f"«{made.fork.thesis}»")
    print(f"  статус: {made.status}; вход: {made.entry}; "
          f"срок: {made.horizon_months} мес; выход: {made.exit}")
    print(f"  катализаторы: {', '.join(made.catalysts) or '—'}")
    print(f"  текущая цена: {b.price:,.2f} {b.price_currency}")
    print(f"  доходность за срок / годовых: "
          f"низ {returns['low'][0]:+.0%} / {returns['low'][1]:+.0%}; "
          f"середина {returns['mid'][0]:+.0%} / {returns['mid'][1]:+.0%}; "
          f"верх {returns['high'][0]:+.0%} / {returns['high'][1]:+.0%}")
    print("  решение: подтвердить идею или отклонить")
    return 0


def cmd_recheck(args):
    """Слой 4: речек — сверка условий активных форков с новыми фактами.

    Форк берётся из замороженного ответа (--fork-answer), речек — из
    --answer. Базовая линия строится так же, как для форка: факты с
    допущениями для банка или объявленные предпосылки для остальных мер.
    """
    entry = _resolve(args.ticker)
    ticker = entry["key"]
    base = _baseline_for_fork(args, ticker)
    if base.is_refusal():
        _print_refusal(base, entry)
        return 3

    forks, problems = fork.parse(_read_text(args.fork_answer))
    if not forks:
        print(f"{ticker} — {entry['name']} — речек")
        print("  ОТКАЗ: форк не разобрать — нечего сверять")
        for p in problems:
            print(f"    · {p.code}: {p.message}")
        return 3

    recheck_data = _load_json(args.answer)
    if not isinstance(recheck_data, dict):
        raise CmdError("ответ речека — JSON-объект с ключом 'forks'")
    raw_forks = recheck_data.get("forks")
    if not isinstance(raw_forks, list) or not raw_forks:
        raise CmdError("ответ речека: ждёт ключ 'forks' со списком сверок")

    try:
        b = fork.basis(base, price=args.price, price_currency=args.quote_currency)
    except ValueError as e:
        raise CmdError(str(e)) from e

    labels = {f.label for f in forks}
    seen = set()
    outcomes = []
    all_refusals = []

    for rf in raw_forks:
        if not isinstance(rf, dict):
            all_refusals.append("элемент forks — не объект")
            continue
        label = rf.get("label")
        if label not in labels:
            all_refusals.append(
                f"метка '{label}' не найдена среди форков — "
                f"доступны: {', '.join(sorted(labels))}")
            continue
        if label in seen:
            all_refusals.append(f"метка '{label}' повторена — сверка один раз")
            continue
        seen.add(label)

        f = next(x for x in forks if x.label == label)
        raw_checks = rf.get("conditions")
        if not isinstance(raw_checks, list):
            all_refusals.append(f"форк '{label}': conditions — не список")
            continue

        checks = []
        for c in raw_checks:
            if not isinstance(c, dict):
                all_refusals.append(f"форк '{label}': условие — не объект")
                continue
            checks.append(recheck.ConditionCheck(
                condition=str(c.get("condition", "")).strip(),
                status=str(c.get("status", "")).strip(),
                fact=str(c.get("fact", "")).strip()))

        outcome = recheck.check(f, b, checks)
        if outcome.is_refusal():
            for r in outcome.refusals:
                all_refusals.append(f"форк '{label}': {r}")
        else:
            outcomes.append(outcome)

    if all_refusals:
        print(f"{ticker} — {entry['name']} — речек")
        print("  ОТКАЗ: сверка не прошла по форме — ничего не записано")
        for r in all_refusals:
            print(f"    · {r}")
        return 3

    print(f"{ticker} — {entry['name']} — речек условий "
          f"при цене {b.price:,.2f} {b.price_currency}")
    for outcome in outcomes:
        _print_recheck_outcome(outcome, b)
        try:
            rid = recheck.record(ticker, outcome)
            print(f"    записано в историю, id={rid}")
        except ValueError as e:
            print(f"    ошибка записи: {e}")

    # Show history after recheck
    events = recheck.timeline(ticker)
    if events:
        print(f"\n  история речеков ({len(events)}):")
        for ev in events:
            req = "требует решения" if ev["requires_decision"] else "в порядке"
            defer = (f", перенос на {ev['deferred_months']} мес"
                     if ev["deferred_months"] else "")
            print(f"    {ev['created_at']}  {ev['fork_label']} — "
                  f"«{ev['fork_thesis']}» — {req}{defer}")
            for c in ev["conditions"]:
                print(f"      [{c['status']}] «{c['condition']}» — {c['fact']}")

    return 0


def _print_recheck_outcome(outcome, b):
    """Один результат речека."""
    print(f"\n  {outcome.fork_label} · «{outcome.fork_thesis}»")
    for c in outcome.conditions:
        mark = {"сработало": "✓", "не сработало": "✗",
                "пока не проверить": "~", "перенесено": "→"}.get(c.status, "?")
        print(f"    {mark} [{c.status}] «{c.condition}»")
        print(f"      факт: {c.fact}")
    if outcome.requires_decision:
        print(f"    решение: человек должен пересмотреть или закрыть форк — "
              f"условие не сработало")
    if outcome.deferred:
        print(f"    перенос: горизонт вырос до {outcome.deferred.horizon_months} мес "
              f"(+{outcome.deferred.horizon_months - outcome.deferred.deferred_from}), "
              f"коридор прежний, годовая доходность ниже")
        if outcome.new_expected:
            e = outcome.new_expected
            print(f"    за {e['years']:g} г. к коридору: "
                  f"низ {e['low'][1]:+.0%} · середина {e['mid'][1]:+.0%} · "
                  f"верх {e['high'][1]:+.0%} годовых")


def cmd_rules_propose(args):
    """Предложить кандидата правила в стандарт оценки.

    Правило приходит с формулировкой, обоснованием и КЕЙСОМ, на котором
    обнаружено — иначе правило невозможно обсуждать.
    """
    try:
        rid = rules.propose(rule_text=args.rule_text,
                           justification=args.justification,
                           case_description=args.case, author=args.author)
    except ValueError as e:
        raise CmdError(str(e)) from e
    print(f"кандидат #{rid} предложен — ждёт решения человека")
    print(f"  правило: {args.rule_text}")
    print(f"  обоснование: {args.justification}")
    print(f"  кейс: {args.case}")
    print(f"  решение: python3 agent/cli.py rules approve {rid}")
    print(f"       или: python3 agent/cli.py rules reject {rid} --reason '...'")
    return 0


def cmd_rules_list(args):
    """Показать очередь кандидатов и историю решений."""
    pending = rules.list_pending()
    approved = rules.list_approved()
    rejected = rules.list_rejected()

    if pending:
        print("— ожидают решения —")
        for r in pending:
            print(f"  #{r['id']}  {r['rule_text']}")
            print(f"      кейс: {r['case_description']}")
            print(f"      предложил: {r['author']}  {r['created_at']}")
            print(f"      решение: python3 agent/cli.py rules approve {r['id']}")
            print(f"           или: python3 agent/cli.py rules reject {r['id']} "
                  f"--reason '...'")
    else:
        print("— ожидают решения —")
        print("  (пусто)")

    if approved:
        print(f"\n— утверждено ({len(approved)}) —")
        for r in approved:
            print(f"  #{r['id']}  {r['rule_text']}")
            print(f"      утвердил: {r['decided_by']}  {r['decided_at']}")

    if rejected:
        print(f"\n— отклонено ({len(rejected)}) —")
        for r in rejected:
            print(f"  #{r['id']}  {r['rule_text']}")
            print(f"      причина: {r['rejection_reason']}")
            print(f"      отклонил: {r['decided_by']}  {r['decided_at']}")

    if not pending and not approved and not rejected:
        print("(правил нет — предложите первое: python3 agent/cli.py rules propose)")

    return 0


def cmd_rules_approve(args):
    """Утвердить кандидата — правило попадает в стандарт оценки."""
    try:
        rules.approve(args.rule_id, decided_by=args.by)
    except ValueError as e:
        raise CmdError(str(e)) from e
    print(f"правило #{args.rule_id} утверждено — записано в стандарт "
          f"({rules.standard_path().name})")
    return 0


def cmd_rules_reject(args):
    """Отклонить кандидата с обязательной причиной."""
    try:
        rules.reject(args.rule_id, reason=args.reason, decided_by=args.by)
    except ValueError as e:
        raise CmdError(str(e)) from e
    print(f"правило #{args.rule_id} отклонено — причина сохранена, "
          f"повторное предложение не пройдёт")
    return 0


def cmd_rules_standard(args):
    """Показать текущий стандарт оценки (утверждённые правила)."""
    path = rules.standard_path()
    if not path.exists():
        print("стандарт оценки ещё не создан — утверждённых правил нет")
        return 0
    print(path.read_text(encoding="utf-8"))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="agent/cli.py", description="Слои v2: профиль бумаги и базовая линия.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("profile", help="показать профиль бумаги")
    p.add_argument("ticker")
    p.set_defaults(fn=cmd_profile)

    p = sub.add_parser("set", help="записать изменение профиля событием")
    p.add_argument("ticker")
    p.add_argument("field")
    p.add_argument("value", help="JSON-список, JSON-строка или просто текст")
    p.add_argument("--reason", required=True,
                   help="почему стало так — без причины событие не пишется")
    p.add_argument("--author", default="model",
                   help="model | research | material | human")
    p.set_defaults(fn=cmd_set)

    p = sub.add_parser("driver", help="ввести драйвер вручную с источником")
    p.add_argument("ticker")
    p.add_argument("driver_name", help="имя драйвера: backlog, contracted_gw, …")
    p.add_argument("value", type=float, help="числовое значение драйвера")
    p.add_argument("--anchor-class", required=True,
                   help="company_guide | macro_guidance | peer_stats | "
                        "own_history | analyst_judgement")
    p.add_argument("--source", default="",
                   help="первоисточник числа; обязателен для всех классов, "
                        "кроме analyst_judgement")
    p.add_argument("--confirms", required=True,
                   help="number | driver | estimate")
    p.add_argument("--reason", required=True,
                   help="почему это значение — без причины событие не пишется")
    p.add_argument("--author", default="human",
                   help="model | research | material | human")
    p.set_defaults(fn=cmd_driver)

    p = sub.add_parser("confirm", help="подтвердить предложенное изменение")
    p.add_argument("ticker")
    p.add_argument("event_id", type=int, help="номер события из вывода profile")
    p.add_argument("--by", default="human", help="кто подтвердил")
    p.set_defaults(fn=cmd_confirm)

    p = sub.add_parser("history", help="история профиля бумаги")
    p.add_argument("ticker")
    p.set_defaults(fn=cmd_history)

    p = sub.add_parser("prisms", help="сток-признаки из фактов и каталог призм")
    p.add_argument("ticker")
    p.add_argument("--facts", help="JSON-файл фактов для расчёта признаков")
    p.set_defaults(fn=cmd_prisms)

    p = sub.add_parser("baseline", help="базовая линия мерой из профиля")
    p.add_argument("ticker")
    p.add_argument("--facts",
                   help="JSON-файл фактов: вход банка; с --assumptions только "
                        "price_context для проверки свежести")
    p.add_argument("--assumptions",
                   help="JSON-файл предпосылок небанковской меры; требует "
                        "--facts для проверки свежести")
    p.add_argument("--rate", type=float,
                   help="ставка дисконтирования, номинальная в валюте отчётности (D20)")
    p.add_argument("--growth", type=float,
                   help="долгосрочный рост, в той же валюте")
    p.add_argument("--rate-why",
                   help="откуда ставка — без происхождения допущение не объявляется")
    p.add_argument("--growth-why", help="откуда рост")
    p.set_defaults(fn=cmd_baseline)

    p = sub.add_parser("drift", help="движение базового коридора между прогонами")
    p.add_argument("ticker")
    p.set_defaults(fn=cmd_drift)

    p = sub.add_parser("images", help="выгрузить картинки-кандидаты из .mhtml "
                                      "(для chart-субагента)")
    p.add_argument("mhtml", help="путь к .mhtml материала")
    p.add_argument("-o", "--out", required=True, help="каталог для PNG + манифест")
    p.set_defaults(fn=cmd_images)

    p = sub.add_parser("show", help="инвентарь канона: без тикера — роестр всех "
                                    "бумаг, с тикером — профиль/baseline/форки/"
                                    "материалы/дрейф")
    p.add_argument("ticker", nargs="?", help="без него — роестр всех бумаг")
    p.add_argument("--detail", action="store_true",
                   help="полная детализация: вывод baseline, тезисы/условия/"
                        "override'ы форков, синтез материала")
    p.add_argument("--explain", action="store_true",
                   help="детализация + директива аналитику написать инвест-вывод "
                        "для человека (три оси, затем /humanizer)")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("add-material", help="внести разбор материала (синтез) "
                                            "в канон — шаг «свежий отчёт»")
    p.add_argument("ticker")
    p.add_argument("--synthesis", required=True,
                   help="файл с выжимкой/синтезом материала")
    p.add_argument("--source-name", help="имя источника (по умолчанию имя файла)")
    p.add_argument("--link", help="ссылка на источник")
    p.add_argument("--author", default=None,
                   help="автор (по умолчанию из INVEST_AUTHOR)")
    p.add_argument("--for-measure", default=None,
                   help="мера разбора (по умолчанию текущая мера профиля)")
    p.set_defaults(fn=cmd_add_material)

    p = sub.add_parser("ingest", help="структурный разбор материала по трём "
                                      "адресатам из готового JSON-split (--answer)")
    p.add_argument("ticker")
    p.add_argument("--answer", required=True,
                   help="файл с JSON-split (profile_updates/baseline_checks/"
                        "thesis_candidates): аналитик готовит его сам, вызова модели нет")
    p.add_argument("--material",
                   help="файл с материалом; без него берутся синтезы канона")
    p.add_argument("--source-name",
                   help="как назвать материал в выводе; по умолчанию имя файла")
    p.add_argument("--facts",
                   help="JSON-файл с фактами: включает сверку чисел материала "
                        "с базовой линией (ingest.compare)")
    p.add_argument("--assumptions",
                   help="предпосылки небанковской меры; --facts при этом "
                        "обязателен для проверки свежести")
    p.add_argument("--rate", type=float)
    p.add_argument("--growth", type=float)
    p.add_argument("--rate-why")
    p.add_argument("--growth-why")
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("research", help="адресный ресёрч-бриф по data_gaps "
                                        "профиля — задание субагенту-ресёрчеру")
    p.add_argument("ticker")
    p.set_defaults(fn=cmd_research)

    p = sub.add_parser("fork", help="тезис о будущем как ветка базовой линии")
    p.add_argument("ticker")
    p.add_argument("--facts",
                   help="JSON-файл с фактами одного отчёта — вход банка")
    p.add_argument("--assumptions",
                   help="JSON-файл с объявленными предпосылками меры — вход "
                        "всех остальных; --facts обязателен рядом и даёт "
                        "только price_context для проверки свежести")
    p.add_argument("--rate", type=float,
                   help="ставка дисконтирования, номинальная в валюте отчётности")
    p.add_argument("--growth", type=float, help="долгосрочный рост")
    p.add_argument("--rate-why", help="откуда ставка")
    p.add_argument("--growth-why", help="откуда рост")
    p.add_argument("--price", type=float,
                   help="цена бумаги, если её нет в базовой линии: меры из "
                        "объявленных предпосылок цену не несут, а без неё не "
                        "видно ни направления форка, ни доли оплаченного тезиса")
    p.add_argument("--quote-currency",
                   help="валюта котировки, если её нет в базовой линии")
    p.add_argument("--answer", required=True,
                   help="файл с замороженным ответом модели: живой вызов — "
                        "следующая команда среза")
    p.set_defaults(fn=cmd_fork)

    p = sub.add_parser("idea", help="ставка на явно выбранный активный форк")
    p.add_argument("ticker")
    p.add_argument("--facts", help="JSON-файл фактов — вход банка")
    p.add_argument("--assumptions", help="JSON-файл предпосылок остальных мер")
    p.add_argument("--rate", type=float)
    p.add_argument("--growth", type=float)
    p.add_argument("--rate-why")
    p.add_argument("--growth-why")
    p.add_argument("--price", type=float)
    p.add_argument("--quote-currency")
    p.add_argument("--fork-answer", required=True,
                   help="замороженный ответ с форками")
    p.add_argument("--fork", required=True, choices=fork.LABELS,
                   help="метка выбранного активного форка")
    p.add_argument("--entry", required=True, help="условие входа")
    p.add_argument("--horizon-months", required=True, type=int,
                   help="срок идеи, не срок форка")
    p.add_argument("--exit", required=True, help="условие выхода")
    p.add_argument("--catalyst", action="append", default=[],
                   help="катализатор; можно повторить")
    p.add_argument("--status", choices=idea.STATUSES, default=idea.WATCHING)
    p.set_defaults(fn=cmd_idea)

    p = sub.add_parser("recheck", help="речек: сверка условий форков с новыми фактами")
    p.add_argument("ticker")
    p.add_argument("--facts",
                   help="JSON-файл с фактами одного отчёта — вход банка")
    p.add_argument("--assumptions",
                   help="JSON-файл с объявленными предпосылками меры — вход "
                        "всех остальных; вместе с --facts не задаётся")
    p.add_argument("--rate", type=float,
                   help="ставка дисконтирования, номинальная в валюте отчётности")
    p.add_argument("--growth", type=float, help="долгосрочный рост")
    p.add_argument("--rate-why", help="откуда ставка")
    p.add_argument("--growth-why", help="откуда рост")
    p.add_argument("--price", type=float,
                   help="цена бумаги, если её нет в базовой линии")
    p.add_argument("--quote-currency",
                   help="валюта котировки, если её нет в базовой линии")
    p.add_argument("--fork-answer", required=True,
                   help="файл с замороженным ответом модели — сами форки")
    p.add_argument("--answer", required=True,
                   help="файл с замороженным ответом речека — сверка условий")
    p.set_defaults(fn=cmd_recheck)

    # --- rules (кандидаты правил в стандарт оценки) ---
    p = sub.add_parser("rules", help="кандидаты правил в стандарт оценки")
    rules_sub = p.add_subparsers(dest="rules_cmd", required=True)

    s = rules_sub.add_parser("propose", help="предложить кандидата правила")
    s.add_argument("--rule-text", required=True, help="формулировка правила")
    s.add_argument("--justification", required=True,
                   help="обоснование — почему правило должно войти в стандарт")
    s.add_argument("--case", required=True,
                   help="кейс, на котором правило обнаружено — без кейса правило "
                        "невозможно обсуждать")
    s.add_argument("--author", default="model", help="model | research | human")
    s.set_defaults(fn=cmd_rules_propose)

    s = rules_sub.add_parser("list", help="показать очередь кандидатов и историю")
    s.set_defaults(fn=cmd_rules_list)

    s = rules_sub.add_parser("approve", help="утвердить кандидата — правило попадёт в стандарт")
    s.add_argument("rule_id", type=int, help="номер кандидата из вывода rules list")
    s.add_argument("--by", default="human", help="кто утвердил")
    s.set_defaults(fn=cmd_rules_approve)

    s = rules_sub.add_parser("reject", help="отклонить кандидата — причина обязательна")
    s.add_argument("rule_id", type=int, help="номер кандидата из вывода rules list")
    s.add_argument("--reason", required=True,
                   help="причина отклонения — без неё правило будет предложено снова")
    s.add_argument("--by", default="human", help="кто отклонил")
    s.set_defaults(fn=cmd_rules_reject)

    s = rules_sub.add_parser("standard", help="показать текущий стандарт оценки")
    s.set_defaults(fn=cmd_rules_standard)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except CmdError as e:
        print(f"ошибка: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
