"""Агент накопления правил — кандидаты, утверждение и отклонение (SPC-008, решение 16).

Агент накапливает знание не только о бумагах, но и о методе: по итогам
прогонов предлагает обобщённое правило, а человек решает, войдёт ли оно в
стандарт оценки.

Кандидат приходит с формулировкой, обоснованием и КЕЙСОМ, на котором
обнаружен — иначе правило невозможно обсуждать. Утверждённое попадает в
стандарт как markdown в git: правится руками и читается в diff.
Неутверждённое остаётся в очереди и на прогоны не влияет. Отклонённый
кандидат сохраняется с причиной отказа, чтобы не предлагался снова.

Пишет в agent/agent.db (таблица rule_candidates), стандарт — в
agent/valuation-rules-standard.md. Боевую invest.db не открывает.
"""
import contextlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "agent.db"
TABLE = "rule_candidates"
STANDARD_FILE = Path(__file__).parent / "valuation-rules-standard.md"


def _db_path():
    """AGENT_RULES_DB — изолированная БД для эвала."""
    override = os.environ.get("AGENT_RULES_DB")
    return Path(override) if override else DB_PATH


def standard_path():
    """AGENT_RULES_STANDARD — изолированный файл стандарта для эвала.

    Публичный сознательно: CLI обязан читать стандарт через него, иначе
    изоляция эвала работает на запись и ломается на чтение.
    """
    override = os.environ.get("AGENT_RULES_STANDARD")
    return Path(override) if override else STANDARD_FILE


def _connect():
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    con.execute(f"""create table if not exists {TABLE} (
        id integer primary key autoincrement,
        created_at text not null,
        rule_text text not null,
        justification text not null,
        case_description text not null,
        status text not null default 'pending',
        rejection_reason text,
        decided_at text,
        decided_by text,
        author text not null default 'model'
    )""")
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


def _now():
    return datetime.now().isoformat(timespec="seconds")


def is_previously_rejected(rule_text):
    """Проверить, не было ли такое же правило уже отклонено.

    Сравнение по тексту правила: если отклонённый кандидат с таким же
    rule_text уже есть, повторное предложение не проходит.
    """
    with _read() as con:
        if con is None:
            return False
        row = con.execute(
            f"select count(*) as cnt from {TABLE} "
            f"where rule_text=? and status='rejected'",
            (rule_text,)).fetchone()
        return row["cnt"] > 0


def propose(rule_text, justification, case_description, author="model"):
    """Предложить кандидата правила.

    Возвращает id нового кандидата. Если такое же правило уже было отклонено,
    поднимает ValueError — повторно предлагать отклонённое нельзя.
    """
    if not (rule_text or "").strip():
        raise ValueError("правило не может быть пустым")
    if not (justification or "").strip():
        raise ValueError("обоснование обязательно")
    if not (case_description or "").strip():
        raise ValueError("кейс обязателен — правило без кейса невозможно обсуждать")

    if is_previously_rejected(rule_text):
        raise ValueError(
            f"правило уже предлагалось и было отклонено: «{rule_text}» — "
            f"повторное предложение не проходит")

    with _write() as con:
        cur = con.execute(
            f"insert into {TABLE} (created_at, rule_text, justification, "
            f"case_description, status, author) values (?,?,?,?,?,?)",
            (_now(), rule_text.strip(), justification.strip(),
             case_description.strip(), "pending", author))
        return cur.lastrowid


def approve(rule_id, decided_by="human"):
    """Утвердить кандидата: переводит в статус approved и записывает в стандарт.

    Возвращает True, если кандидат нашёлся и был в статусе pending.
    """
    with _write() as con:
        row = con.execute(
            f"select * from {TABLE} where id=? and status='pending'",
            (rule_id,)).fetchone()
        if row is None:
            raise ValueError(f"кандидат #{rule_id} не найден или уже "
                           f"не в статусе pending")
        con.execute(
            f"update {TABLE} set status='approved', decided_at=?, decided_by=? "
            f"where id=?",
            (_now(), decided_by, rule_id))
    _write_standard()
    return True


def reject(rule_id, reason, decided_by="human"):
    """Отклонить кандидата с обязательной причиной.

    Возвращает True, если кандидат нашёлся и был в статусе pending.
    """
    if not (reason or "").strip():
        raise ValueError("причина отклонения обязательна — иначе правило "
                         "будет предложено снова")
    with _write() as con:
        row = con.execute(
            f"select * from {TABLE} where id=? and status='pending'",
            (rule_id,)).fetchone()
        if row is None:
            raise ValueError(f"кандидат #{rule_id} не найден или уже "
                           f"не в статусе pending")
        con.execute(
            f"update {TABLE} set status='rejected', rejection_reason=?, "
            f"decided_at=?, decided_by=? where id=?",
            (reason.strip(), _now(), decided_by, rule_id))
    return True


def list_pending():
    """Все кандидаты в очереди на решение."""
    return _list_by_status("pending")


def list_approved():
    """Все утверждённые правила."""
    return _list_by_status("approved")


def list_rejected():
    """Все отклонённые правила."""
    return _list_by_status("rejected")


def _list_by_status(status):
    with _read() as con:
        if con is None:
            return []
        rows = con.execute(
            f"select * from {TABLE} where status=? order by id", (status,))
        return [dict(r) for r in rows]


def _write_standard():
    """Дописать утверждённые правила, которых ещё нет в markdown-стандарте.

    Стандарт — это markdown-файл в git, который правится руками и читается
    в diff. Правила дописываются в конец файла, а не перезаписывают его:
    ручные правки между утверждениями не теряются. Нумерация сквозная по
    содержимому файла. В файл дописываются ВСЕ утверждённые, которых там
    нет: порядок утверждения не решает, какое правило дойдёт до стандарта.
    """
    approved = list_approved()
    if not approved:
        return
    path = standard_path()

    if not path.exists():
        path.write_text(_SEED_STANDARD, encoding="utf-8")

    existing = path.read_text(encoding="utf-8")

    # Не дублируем: правило, уже попавшее в файл, не пишется повторно.
    missing = [rule for rule in approved if rule["rule_text"] not in existing]
    if not missing:
        return

    # Если в файле есть заглушка «правил пока нет», убираем её.
    clean = existing.replace("_Утверждённых правил пока нет._\n", "")

    # Считаем, сколько правил уже в файле — по заголовкам ### Правило.
    number = clean.count("### Правило ")
    for rule in missing:
        number += 1
        entry = [
            "",
            f"### Правило {number}: {rule['rule_text']}",
            "",
            f"**Обоснование:** {rule['justification']}",
            "",
            f"**Кейс:** {rule['case_description']}",
            "",
            f"_Утверждено {rule['decided_at']} — {rule['decided_by']}_",
            "",
        ]
        clean = clean.rstrip("\n") + "\n".join(entry) + "\n"

    # Атомарно: читатель стандарта не должен видеть половину дописанного.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(clean, encoding="utf-8")
    tmp.replace(path)


_SEED_STANDARD = (
    "# Стандарт оценки — утверждённые правила (v2)\n"
    "\n"
    "> Правила, утверждённые человеком по итогам прогонов агента.\n"
    "> Каждое правило входит с измеренным кейсом — нет кейса, нет правила.\n"
    "> Файл правится руками и читается в diff. Автоматически дописывается\n"
    "> командой `rules approve`.\n"
    "\n"
    "---\n"
    "\n"
    "_Утверждённых правил пока нет._\n"
)