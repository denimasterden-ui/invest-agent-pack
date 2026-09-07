"""Хранилище событий профиля — слой 0 v2 (SPC-008).

Профиль меняется: comps уточняются, данные наконец находятся (развёрнутые МВт
у NBIS), бизнес меняет природу (IREN переезжает из майнинга в AI-хостинг),
компания покупает доли и становится холдингом.

Устройство то же, что уже работает в проде для допущений и призм
(db.write_assumption_events / db.replay_assumptions): пишутся СОБЫТИЯ,
актуальный профиль собирается реплеем. История отвечает на вопрос «когда и
почему стало так», а не только «как сейчас».

Содержание события (поле, старое и новое значение, причина, автор) никогда не
редактируется. Подтверждение меняет только статус предложения и дописывает
решение человека (confirmed_at/confirmed_by) к тому событию, которое оно
закрывает: предложение и решение о нём — одна строка истории.

Смена меры — событие особого веса. Playbook: «смена метода меняет результат
сильнее, чем любое допущение внутри метода» (прецедент NBIS 17.07 -> 28.07,
разрыв 3.5x). Поэтому она записывается неподтверждённой и в состояние не
попадает, пока человек не подтвердит конкретное событие. Остальные поля
подтверждения не требуют. Неподтверждённое предложение остаётся в истории
как отклонённое: в состояние оно и так не попадает.

Пишет в собственный agent/agent.db — боевую invest.db слой не открывает.
Журнал ничего не знает о профиле: он хранит то, что ему передали. Прежнее
значение изменения достоверно знает только сам профиль (даже когда это seed,
а не прошлое событие), поэтому запись идёт через profile.record_change.
"""
import contextlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

# Поля, которые событие может перекрыть. ticker/research_type/core — не знание
# о бизнесе, а способ его собрать: они выводятся, а не запоминаются.
OVERRIDABLE = ("business_kind", "measure", "measure_reason", "drivers",
               "caps", "comps", "multiple_metric", "data_gaps", "driver_facts",
               "capital_signals", "price_context", "segments", "scope")
LIST_FIELDS = ("drivers", "caps", "comps", "data_gaps")
DICT_FIELDS = ("driver_facts",)
PROFILE_DICT_FIELDS = ("capital_signals", "price_context")

# Обязательные ключи внутри driver_fact — без любого из них драйвер неполон.
DRIVER_FACT_KEYS = ("value", "anchor_class", "source", "confirms", "reason")

# Словарь авторов — решение 3 спеки: модель, ресёрч, материал, человек.
AUTHORS = ("model", "research", "material", "human")

DB_PATH = Path(__file__).parent / "agent.db"
MEASURE_FIELD = "measure"
CONFIRM_FIELDS = (MEASURE_FIELD, "scope")
EVENTS = "profile_events"


def _db_path():
    """AGENT_PROFILE_DB — изолированная БД для эвала и прогона команды."""
    override = os.environ.get("AGENT_PROFILE_DB")
    return Path(override) if override else DB_PATH


def _connect():
    """Соединение для записи. Схема создаётся здесь и только здесь."""
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    con.execute(f"""create table if not exists {EVENTS} (
        id integer primary key autoincrement,
        ticker text not null, created_at text not null,
        field text not null, old_value text, new_value text not null,
        reason text not null, author text not null,
        confirmed integer not null default 1,
        confirmed_at text, confirmed_by text)""")
    con.execute("""create table if not exists baselines (
        id integer primary key autoincrement,
        ticker text not null, run_at text not null, measure text not null,
        corridor_low real, corridor_high real,
        assumptions_json text not null, status text not null)""")
    con.execute("create index if not exists idx_baselines_ticker_run "
                "on baselines(ticker, run_at, id)")
    con.execute("""create table if not exists forks (
        id integer primary key autoincrement,
        ticker text not null, run_at text not null, measure text not null,
        label text not null, corridor_low real, corridor_high real,
        assumptions_json text not null, status text not null)""")
    con.execute("create index if not exists idx_forks_ticker_run "
                "on forks(ticker, run_at, id)")
    return con


@contextlib.contextmanager
def _write():
    """Соединение для записи с закрытием: схема создаётся тут и только тут."""
    con = _connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


@contextlib.contextmanager
def _read():
    """Соединение для чтения: журнал, которого ещё нет, пуст, а не создаётся.

    Пустого журнала мало: файл БД общий с другими слоями, и `rules` создаёт
    его первым — тогда файл есть, а таблицы профиля в нём нет. Проверяется
    именно таблица, а не файл: иначе первая же команда правил ломает чтение
    профиля трассировкой вместо пустого состояния.
    """
    path = _db_path()
    if not path.exists():
        yield None
        return
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        exists = con.execute(
            "select 1 from sqlite_master where type='table' and name=?",
            (EVENTS,)).fetchone()
        yield con if exists else None
    finally:
        con.close()


def record(ticker, field, new_value, reason, author="model", old_value=None):
    """Записать изменение профиля. Смена меры приходит неподтверждённой.

    Возвращает признак того, что изменение уже в силе: остальные поля
    применяются сразу, мера ждёт человека.
    """
    _validate(field, new_value, reason, author)
    applied = field not in CONFIRM_FIELDS
    with _write() as con:
        con.execute(
            f"insert into {EVENTS} (ticker, created_at, field, old_value,"
            f" new_value, reason, author, confirmed) values (?,?,?,?,?,?,?,?)",
            (ticker, _now(), field, _dump(old_value), _dump(new_value),
             reason, author, 1 if applied else 0))
    return applied


def confirm(ticker, event_id, author="human"):
    """Подтвердить конкретное предложение — только так смена меры вступает в силу.

    Подтверждено ровно то, что человек подтвердил; остальные предложения того
    же поля остаются неподтверждёнными. Возвращает 1, если предложение нашлось.
    """
    author = (author or "").strip() or "human"
    with _write() as con:
        cur = con.execute(
            f"update {EVENTS} set confirmed=1, confirmed_at=?, confirmed_by=? "
            f"where ticker=? and id=? and confirmed=0",
            (_now(), author, ticker, event_id))
        return cur.rowcount


def replay(ticker):
    """Собрать актуальный профиль из подтверждённых событий.

    Возвращает (состояние, предложения). Предложения — неподтверждённые
    события по порядку записи; в состояние не попадает ни одно из них, пока
    человек не подтвердит конкретное.
    """
    state, pending = {}, []
    with _read() as con:
        if con is None:
            return state, pending
        rows = con.execute(
            f"select * from {EVENTS} where ticker=? order by id", (ticker,))
        for r in rows:
            if r["confirmed"]:
                value = json.loads(r["new_value"])
                if r["field"] == "scope":
                    value = {**value, "confirmed_by": r["confirmed_by"]}
                state[r["field"]] = value
            else:
                pending.append(dict(id=r["id"], field=r["field"],
                                    value=json.loads(r["new_value"]),
                                    reason=r["reason"], author=r["author"],
                                    created_at=r["created_at"]))
    return state, pending


def timeline(ticker):
    """Все события тикера по порядку записи. Значения возвращаются как
    объекты, а не в формате хранения: журнал — не про JSON."""
    with _read() as con:
        if con is None:
            return []
        rows = con.execute(
            f"select * from {EVENTS} where ticker=? order by id", (ticker,))
        events = [dict(r) for r in rows]
    for r in events:
        for key in ("old_value", "new_value"):
            if r[key] is not None:
                r[key] = json.loads(r[key])
    return events


def _validate(field, new_value, reason, author):
    if field not in OVERRIDABLE:
        raise ValueError(f"{field}: не поле профиля — перекрыть можно "
                         f"{', '.join(OVERRIDABLE)}")
    if author not in AUTHORS:
        raise ValueError(f"{author}: автор не из словаря — "
                         f"{' | '.join(AUTHORS)}")
    if not (reason or "").strip():
        raise ValueError("событие без причины не пишется: история должна "
                         "отвечать, почему стало так")
    if field == "comps":
        if not isinstance(new_value, (list, tuple)) or not all(
                _is_comp_item(item) for item in new_value):
            raise ValueError("comps: ждёт список строк или словарей "
                             f"{{name,segment,kind,valuation_ref}}, "
                             f"пришло {new_value!r}")
    elif field in LIST_FIELDS:
        if not _is_string_list(new_value):
            raise ValueError(f"{field}: ждёт список непустых строк, "
                             f"пришло {new_value!r}")
    elif field == "segments":
        if not isinstance(new_value, (list, tuple)) or not all(
                isinstance(segment, dict)
                and isinstance(segment.get("name"), str)
                and segment["name"].strip()
                for segment in new_value):
            raise ValueError("segments: ждёт список объектов с непустым name")
    elif field == "scope":
        if (not isinstance(new_value, dict)
                or set(new_value) != {"measure", "reason"}
                or not all(isinstance(new_value.get(key), str)
                           and new_value[key].strip()
                           for key in ("measure", "reason"))):
            raise ValueError("scope: ждёт объект {measure, reason} без confirmed_by")
    elif field in DICT_FIELDS:
        if not isinstance(new_value, dict):
            raise ValueError(f"{field}: ждёт словарь driver_name → {{value, "
                             f"anchor_class, source, confirms, reason}}, "
                             f"пришло {type(new_value).__name__}")
        for name, fact in new_value.items():
            if not isinstance(fact, dict):
                raise ValueError(f"{field}: {name!r} — ждёт словарь с ключами "
                                 f"{', '.join(DRIVER_FACT_KEYS)}, "
                                 f"пришло {type(fact).__name__}")
            missing = [k for k in DRIVER_FACT_KEYS if k not in fact]
            if missing:
                raise ValueError(f"{field}: {name!r} — нет ключей "
                                 f"{', '.join(missing)}")
            if not _is_number(fact.get("value")):
                raise ValueError(f"{field}: {name!r} — value ждёт число, "
                                 f"пришло {fact.get('value')!r}")
    elif field in PROFILE_DICT_FIELDS:
        if not isinstance(new_value, dict):
            raise ValueError(f"{field}: ждёт словарь, "
                             f"пришло {type(new_value).__name__}")
    elif not isinstance(new_value, str) or not new_value.strip():
        raise ValueError(f"{field}: ждёт непустую строку, пришло {new_value!r}")


def _is_string_list(value):
    return (isinstance(value, (list, tuple))
            and all(isinstance(v, str) and v.strip() for v in value))


def _is_comp_item(item):
    """A comp is a legacy non-empty ticker string or a structured dict."""
    if isinstance(item, str):
        return bool(item.strip())
    return isinstance(item, dict) and isinstance(item.get("name"), str) \
        and bool(item["name"].strip())


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def driver_facts(ticker):
    """Собрать driver_facts-словарь из подтверждённых событий."""
    state, _ = replay(ticker)
    return state.get("driver_facts", {})


def record_baseline(ticker, measure, corridor, assumptions, status, run_at=None):
    """Append one baseline outcome; refusals deliberately remain in history."""
    low, high = corridor if corridor is not None else (None, None)
    with _write() as con:
        cur = con.execute(
            "insert into baselines (ticker, run_at, measure, corridor_low, "
            "corridor_high, assumptions_json, status) values (?,?,?,?,?,?,?)",
            (ticker, run_at or _now(), measure, low, high, _dump(assumptions), status))
        return cur.lastrowid


def record_fork(ticker, measure, label, corridor, assumptions, status, run_at=None):
    """Append one evaluated fork, including refused and non-bettable outcomes."""
    low, high = corridor if corridor is not None else (None, None)
    with _write() as con:
        cur = con.execute(
            "insert into forks (ticker, run_at, measure, label, corridor_low, "
            "corridor_high, assumptions_json, status) values (?,?,?,?,?,?,?,?)",
            (ticker, run_at or _now(), measure, label, low, high,
             _dump(assumptions), status))
        return cur.lastrowid


def baseline_drift(ticker):
    """Successful baseline runs with movement from the preceding corridor."""
    with _read() as con:
        if con is None:
            return []
        try:
            rows = con.execute(
                "select id, run_at, measure, corridor_low, corridor_high "
                "from baselines where ticker=? and status='calculated' "
                "order by id", (ticker,)).fetchall()
        except sqlite3.OperationalError:
            # A pre-SPC-009 database is a valid empty history for read commands.
            return []
    out = []
    previous = None
    for row in rows:
        item = dict(row)
        current = (item["corridor_low"], item["corridor_high"])
        old_mid = sum(previous) / 2 if previous else None
        new_mid = sum(current) / 2
        item["previous_corridor"] = previous
        item["drift"] = ((new_mid - old_mid) / abs(old_mid)
                         if old_mid not in (None, 0) else None)
        item["corridor"] = current
        out.append(item)
        previous = current
    return out


def get_scenarios(ticker):
    """Latest calculated baseline and its locally persisted fork run."""
    with _read() as con:
        if con is None:
            return {"baseline": None, "forks": []}
        try:
            baseline_row = con.execute(
                "select run_at, measure, corridor_low, corridor_high "
                "from baselines where ticker=? and status='calculated' "
                "order by run_at desc, id desc limit 1", (ticker,)).fetchone()
            if baseline_row is None:
                return {"baseline": None, "forks": []}
            fork_rows = con.execute(
                "select label, corridor_low, corridor_high, assumptions_json, run_at "
                "from forks where ticker=? and run_at>=? and status!='refused' "
                "and corridor_low is not null and corridor_high is not null "
                "order by run_at, id",
                (ticker, baseline_row["run_at"]),
            ).fetchall()
        except sqlite3.OperationalError:
            return {"baseline": None, "forks": []}

    baseline = {
        "corridor": [baseline_row["corridor_low"], baseline_row["corridor_high"]],
        "measure": baseline_row["measure"],
        "run_at": baseline_row["run_at"],
    }
    # Форки append-only: пере-прогон копит поколения (v1..vN) в таблице как
    # историю. Активный набор — последний форк на каждый лейбл. Строки
    # отсортированы по (run_at, id) возрастающе, поэтому последняя запись
    # лейбла затирает предыдущие.
    latest = {}
    for row in fork_rows:
        assumptions = json.loads(row["assumptions_json"])
        latest[row["label"]] = {
            "label": row["label"],
            "channel": assumptions.get("channel", ""),
            "corridor": [row["corridor_low"], row["corridor_high"]],
            "run_at": row["run_at"],
        }
    return {"baseline": baseline, "forks": list(latest.values())}


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _dump(value):
    return json.dumps(value, ensure_ascii=False)
