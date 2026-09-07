"""Слой 4 v2 — речек: сверка условий активных форков с новыми фактами (SPC-008).

Каждое условие получает статус и одну строку факта. Статусов четыре, и
четвёртый добавлен по опыту прототипа: сработало, не сработало, пока не
проверить, ПЕРЕНЕСЕНО. Перенос — не поломка тезиса: событие ожидается позже,
коридор остаётся прежним, но горизонт растёт и годовая доходность падает.
Без этого статуса сдвиг срока пришлось бы записывать как провал.

Несработавшее условие помечает форк требующим решения — пересмотр или
закрытие. Решение принимает человек, автоматических действий нет.

Результат сверки пишется в память и виден в истории: эволюция тезиса должна
читаться так же, как эволюция допущений.

В речеке форк не меняет тезис и условия — пересчитываются только предпосылки
и коридор.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import fork as fork_mod

# ── статусы условий ────────────────────────────────────────────────────────────

TRIGGERED = "сработало"
NOT_TRIGGERED = "не сработало"
CANT_CHECK = "пока не проверить"
POSTPONED = "перенесено"
STATUSES = (TRIGGERED, NOT_TRIGGERED, CANT_CHECK, POSTPONED)

# ── БД речека ──────────────────────────────────────────────────────────────────

DB_PATH = Path(__file__).parent / "agent.db"
RECHECK_EVENTS = "recheck_events"


def _db_path():
    override = os.environ.get("AGENT_PROFILE_DB")
    return Path(override) if override else DB_PATH


def _connect():
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    con.execute(f"""create table if not exists {RECHECK_EVENTS} (
        id integer primary key autoincrement,
        ticker text not null, created_at text not null,
        fork_label text not null, fork_thesis text not null,
        conditions_json text not null,
        requires_decision integer not null default 0,
        deferred_months integer not null default 0,
        new_horizon_months integer,
        expected_json text)""")
    return con


@contextlib.contextmanager
def _write():
    con = _connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


@contextlib.contextmanager
def _read():
    path = _db_path()
    if not path.exists():
        yield None
        return
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        yield con
    finally:
        con.close()


# ── результат сверки одного условия ────────────────────────────────────────────

@dataclass(frozen=True)
class ConditionCheck:
    """Одно проверенное условие форка."""
    condition: str
    status: str
    fact: str


# ── результат сверки форка ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class RecheckOutcome:
    """Исход речека: статусы условий, пометка форка, возможный перенос."""
    ticker: str
    fork_label: str
    fork_thesis: str
    conditions: tuple                 # ConditionCheck — по одному на каждое условие форка
    requires_decision: bool           # True → человек должен решить судьбу форка
    deferred: fork_mod.Fork | None    # None → переноса не было
    refusals: tuple = ()              # ошибки валидации — пусто → речек состоялся
    new_expected: dict | None = None  # доходность после переноса, если был

    def is_refusal(self):
        return bool(self.refusals)


# ── сверка ─────────────────────────────────────────────────────────────────────

def check(fork, basis, condition_checks):
    """Сверить условия форка с новыми фактами.

    Принимает форк, его базовую линию и список проверок — по одной на каждое
    условие форка, в том же порядке. Каждая проверка несёт текст условия
    (должен совпадать с форком), статус и одну строку факта.

    Возвращает RecheckOutcome: статусы, пометку и, если был перенос, —
    отложенный форк с пересчитанной доходностью.
    """
    refusals = []
    # Условия не должны меняться между объявлением и речеком.
    expected = fork.must_be_true
    if len(condition_checks) != len(expected):
        refusals.append(
            f"условий в речеке {len(condition_checks)}, в форке {len(expected)} — "
            f"речек не меняет набор условий")
    else:
        for i, (check_item, exp) in enumerate(zip(condition_checks, expected)):
            if check_item.condition != exp:
                refusals.append(
                    f"условие #{i + 1} изменено: было «{exp}», "
                    f"в речеке «{check_item.condition}» — "
                    f"речек не меняет условия форка")
            if check_item.status not in STATUSES:
                refusals.append(
                    f"условие #{i + 1}: неизвестный статус "
                    f"«{check_item.status}» — статусов четыре: "
                    f"{', '.join(STATUSES)}")
            if not check_item.fact.strip() or "\n" in check_item.fact.strip():
                refusals.append(
                    f"условие #{i + 1}: факт должен быть одной непустой строкой")

    if refusals:
        return RecheckOutcome(
            ticker=basis.ticker, fork_label=fork.label,
            fork_thesis=fork.thesis, conditions=tuple(condition_checks),
            requires_decision=False, deferred=None, refusals=tuple(refusals))

    requires_decision = any(c.status == NOT_TRIGGERED for c in condition_checks)

    # Перенос: горизонт растёт, коридор тот же, годовая доходность падает.
    deferred = None
    new_expected = None
    postponed_months = sum(
        12 for c in condition_checks if c.status == POSTPONED)
    if postponed_months > 0:
        deferred = fork_mod.defer(fork, postponed_months)
        # Пересчитываем коридор с теми же предпосылками — он не меняется,
        # но expected_return зависит от горизонта.
        res = fork_mod.evaluate(basis, deferred)
        if not res.is_refusal():
            new_expected = res.expected

    return RecheckOutcome(
        ticker=basis.ticker, fork_label=fork.label,
        fork_thesis=fork.thesis, conditions=tuple(condition_checks),
        requires_decision=requires_decision, deferred=deferred,
        new_expected=new_expected)


def at_generation(answer, forks, basis):
    """Сверить ``recheck`` из ответа генератора с условиями форков.

    Ошибки этой дополнительной сверки возвращаются отдельно: они не меняют
    контракт валидации/ retry генератора и не мешают посчитать сам форк.
    """
    fenced = re.search(r"```(?:json)?\s*(.*?)```", answer, re.S | re.I)
    raw = fenced.group(1) if fenced else answer
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return (), ("ответ не содержит JSON для recheck",)
    try:
        data = json.loads(raw[start:end + 1])
    except (TypeError, ValueError) as exc:
        return (), (f"recheck не разобран: {exc}",)

    items = data.get("forks") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return (), ("ответ не содержит forks для recheck",)

    by_label = {item.get("label"): item for item in items
                if isinstance(item, dict)}
    outcomes = []
    problems = []
    for branch in forks:
        item = by_label.get(branch.label)
        raw_checks = item.get("recheck") if isinstance(item, dict) else None
        if not isinstance(raw_checks, list):
            problems.append(f"{branch.label}: recheck не объявлен")
            continue
        checks = []
        for raw_check in raw_checks:
            if not isinstance(raw_check, dict):
                problems.append(f"{branch.label}: элемент recheck — не объект")
                continue
            checks.append(ConditionCheck(
                condition=str(raw_check.get("condition", "")).strip(),
                status=str(raw_check.get("status", "")).strip(),
                fact=str(raw_check.get("fact", "")).strip()))
        outcome = check(branch, basis, checks)
        if outcome.is_refusal():
            problems.extend(f"{branch.label}: {p}" for p in outcome.refusals)
        else:
            outcomes.append(outcome)
    return tuple(outcomes), tuple(problems)


# ── запись в память ────────────────────────────────────────────────────────────

def record(ticker, outcome):
    """Записать результат речека в историю. Возвращает id записи."""
    if outcome.is_refusal():
        raise ValueError("отказавший речек не пишется: "
                         + "; ".join(outcome.refusals))
    deferred_months = (outcome.deferred.horizon_months
                       - outcome.deferred.deferred_from
                       if outcome.deferred and outcome.deferred.deferred_from
                       else 0)
    new_horizon = (outcome.deferred.horizon_months
                   if outcome.deferred else None)
    with _write() as con:
        cur = con.execute(
            f"insert into {RECHECK_EVENTS} (ticker, created_at, fork_label, "
            f"fork_thesis, conditions_json, requires_decision, "
            f"deferred_months, new_horizon_months, expected_json) "
            f"values (?,?,?,?,?,?,?,?,?)",
            (ticker, _now(),
             outcome.fork_label, outcome.fork_thesis,
             json.dumps([{
                 "condition": c.condition, "status": c.status,
                 "fact": c.fact,
             } for c in outcome.conditions], ensure_ascii=False),
             int(outcome.requires_decision),
             deferred_months,
             new_horizon,
             json.dumps(outcome.new_expected, ensure_ascii=False)
             if outcome.new_expected else None))
        return int(cur.lastrowid)


def timeline(ticker):
    """Все речеки тикера по порядку записи — история эволюции тезиса."""
    with _read() as con:
        if con is None:
            return []
        rows = con.execute(
            f"select * from {RECHECK_EVENTS} where ticker=? order by id",
            (ticker,)).fetchall()
    out = []
    for r in rows:
        row = dict(r)
        row["conditions"] = json.loads(row["conditions_json"])
        row["expected"] = (json.loads(row["expected_json"])
                           if row["expected_json"] else None)
        out.append(row)
    return out


def _now():
    return datetime.now().isoformat(timespec="seconds")
