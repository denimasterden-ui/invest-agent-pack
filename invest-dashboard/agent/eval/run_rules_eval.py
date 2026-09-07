#!/usr/bin/env python3
"""Offline contract eval for rules agent — кандидаты правил в стандарт оценки.

Проверяет:
- кандидат кладётся с кейсом и не влияет на прогоны;
- утверждение переносит правило в стандарт;
- отклонение сохраняет причину;
- повторное предложение отклонённого правила не проходит.

Три правила прототипа — рабочий материал для проверки механизма:
  1. числа берутся согласованно из одного отчёта (смешанные из разных
     источников дали худшую сходимость мер);
  2. премия к сопоставимым требует объявления;
  3. bull не может быть ниже базовой линии.

Эвал гоняется на фикстурах, без сети. Журнал правил подменяется
(AGENT_RULES_DB) — боевая agent.db не затрагивается.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import rules  # noqa: E402


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main():
    with tempfile.TemporaryDirectory() as td:
        os.environ["AGENT_RULES_DB"] = str(Path(td) / "rules.db")
        std_path = Path(td) / "valuation-rules-standard.md"
        os.environ["AGENT_RULES_STANDARD"] = str(std_path)

        # --- Контракт: кандидат кладётся с кейсом ---
        rid = rules.propose(
            rule_text="числа берутся согласованно из одного отчёта",
            justification="смешанные из разных источников дали худшую "
                          "сходимость мер",
            case_description="KSPI 2К26: баланс из одного отчёта дал "
                             "сходимость DDM/PBv ~1.1%, смешанные — ~4%",
            author="model")
        check("кандидат создаётся и получает id", isinstance(rid, int) and rid > 0)

        pending = rules.list_pending()
        check("кандидат виден в очереди", len(pending) == 1 and pending[0]["id"] == rid)
        check("кандидат несёт формулировку", pending[0]["rule_text"] == "числа берутся согласованно из одного отчёта")
        check("кандидат несёт обоснование", "смешанные из разных" in pending[0]["justification"])
        check("кандидат несёт кейс", "KSPI 2К26" in pending[0]["case_description"])
        check("кандидат в статусе pending", pending[0]["status"] == "pending")

        # --- Контракт: утверждение переносит правило в стандарт ---
        rules.approve(rid, decided_by="human")
        check("после утверждения кандидат не в pending", len(rules.list_pending()) == 0)
        approved = rules.list_approved()
        check("утверждённое правило видно в approved", len(approved) == 1)
        check("утверждённое правило в статусе approved", approved[0]["status"] == "approved")
        check("у утверждённого есть decided_by", approved[0]["decided_by"] == "human")
        check("у утверждённого есть decided_at", approved[0]["decided_at"] is not None)

        std = std_path.read_text(encoding="utf-8")
        check("правило попало в стандарт (markdown)", "согласованно из одного отчёта" in std)
        check("кейс в стандарте", "KSPI 2К26" in std)

        # --- Контракт: отклонение сохраняет причину ---
        rid2 = rules.propose(
            rule_text="премия к сопоставимым требует объявления",
            justification="без объявления премия — необоснованное суждение",
            case_description="DELL: forward P/E 20.37x при полосе сопоставимых "
                             "6.96–12.81x — премия +59% не объявлена",
            author="model")
        rules.reject(rid2, reason="не ясно, как измерять премию — нужен "
                     "метод расчёта", decided_by="human")
        check("после отклонения кандидат не в pending", len(rules.list_pending()) == 0)
        rejected = rules.list_rejected()
        check("отклонённое правило видно в rejected", len(rejected) == 1)
        check("отклонённое правило сохраняет причину", "как измерять премию" in rejected[0]["rejection_reason"])
        check("у отклонённого есть decided_by", rejected[0]["decided_by"] == "human")

        # --- Контракт: повторное предложение отклонённого правила не проходит ---
        rejected_text = "премия к сопоставимым требует объявления"
        check("повторное предложение отклонённого правила не проходит",
              rules.is_previously_rejected(rejected_text))

        try:
            rules.propose(
                rule_text=rejected_text,
                justification="повтор",
                case_description="повторный кейс",
                author="model")
            check("повторное предложение падает исключением", False)
        except ValueError as e:
            check("повторное предложение падает исключением",
                  "отклонено" in str(e).lower() or "уже предлагалось" in str(e).lower())

        # --- Третье правило прототипа ---
        rid3 = rules.propose(
            rule_text="bull не может быть ниже базовой линии",
            justification="оптимистичный сценарий хуже базового — ошибка, "
                          "а не результат",
            case_description="NBIS: bull-форк дал середину коридора ниже "
                             "середины базовой линии — метка не соответствует "
                             "содержанию",
            author="model")
        check("третье правило создаётся", isinstance(rid3, int) and rid3 > 0)

        # --- Контракт: кандидат не влияет на прогоны ---
        # Пока кандидат в pending, он не в стандарте — значит, не влияет.
        std2 = std_path.read_text(encoding="utf-8")
        check("неутверждённый кандидат не в стандарте",
              "bull не может быть ниже базовой" not in std2)

        # --- Утверждаем и проверяем, что все три могут быть в стандарте ---
        rules.approve(rid3, decided_by="human")
        std3 = std_path.read_text(encoding="utf-8")
        check("после утверждения правило в стандарте",
              "bull не может быть ниже" in std3)

        # --- Итог: три правила прототипа учтены ---
        check("в стандарте два утверждённых правила",
              std3.count("### ") == 2)

    print("All rules evals passed")


if __name__ == "__main__":
    main()