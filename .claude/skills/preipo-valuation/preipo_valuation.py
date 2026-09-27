#!/usr/bin/env python3
"""preipo-valuation — оценка частной (pre-IPO) компании через наш Burry-аналитик.

Метод Burry-аналитика (method_burry.md) + задача pre-IPO: выход в терминах EV на выходе + IRR от входа, а не цены акции.
Вход — файл с фактами/тезисом компании (включая условия сделки).

Использование:
  python3 preipo_valuation.py <context.md> [--name NAME]
"""
import os, argparse, warnings
warnings.filterwarnings("ignore")

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description="Burry pre-IPO оценка частной компании")
    ap.add_argument("context", help="файл .md/.txt с фактами/тезисом (включая условия сделки)")
    ap.add_argument("--name", default=None, help="имя компании (по умолчанию — из имени файла)")
    args = ap.parse_args()

    name = args.name or os.path.splitext(os.path.basename(args.context))[0]
    context = open(args.context, encoding="utf-8").read()
    method = os.path.join(SKILL_DIR, "method_burry.md")
    if not os.path.exists(method):
        raise SystemExit("Нет method_burry.md: метод оценки не входит в пак — положи свой рядом со скриптом.")
    system_prompt = open(method, encoding="utf-8").read()
    task = open(os.path.join(SKILL_DIR, "task_preipo.md"), encoding="utf-8").read()

    # v2: аналитик — сам Claude Code (без OpenRouter). Скрипт печатает метод +
    # контекст; Burry pre-IPO оценку (EV на выходе + IRR + margin of safety)
    # прогоняет аналитик в ответе.
    bar = "=" * 72
    print(f"{bar}\nBURRY PRE-IPO ОЦЕНКА — {name}\n"
          "Аналитик (Claude Code): по методу ниже дай Bear/Base/Bull EV на выходе,\n"
          "IRR от входа и вердикт margin of safety.\n"
          f"{bar}\n=== МЕТОД (method_burry) ===\n{system_prompt}\n\n"
          f"=== ЗАДАЧА (task_preipo) ===\n{task}\n\n"
          f"=== КОНТЕКСТ КОМПАНИИ ({name}) ===\n{context}")


if __name__ == "__main__":
    main()
