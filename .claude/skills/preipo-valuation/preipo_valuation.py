#!/usr/bin/env python3
"""preipo-valuation — оценка частной (pre-IPO) компании через наш Burry-аналитик.

Тот же системный промпт (system_burry.md), что генерит Bear/Base/Bull для портфеля,
адаптированный под pre-IPO: выход в терминах EV на выходе + IRR от входа, а не цены акции.
Вход — файл с фактами/тезисом компании (включая условия сделки).

Использование:
  /usr/bin/python3 preipo_valuation.py <context.md> [--name NAME] [--max-tokens N]
"""
import sys, os, argparse, warnings
warnings.filterwarnings("ignore")

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD = os.path.normpath(os.path.join(SKILL_DIR, "..", "..", "..", "invest-dashboard"))
sys.path.insert(0, DASHBOARD)

from agent.kernel.research import _load_prompt               # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Burry pre-IPO оценка частной компании")
    ap.add_argument("context", help="файл .md/.txt с фактами/тезисом (включая условия сделки)")
    ap.add_argument("--name", default=None, help="имя компании (по умолчанию — из имени файла)")
    args = ap.parse_args()

    name = args.name or os.path.splitext(os.path.basename(args.context))[0]
    context = open(args.context, encoding="utf-8").read()
    system_prompt = _load_prompt("system_burry.md")
    task = open(os.path.join(SKILL_DIR, "task_preipo.md"), encoding="utf-8").read()

    # v2: аналитик — сам Claude Code (без OpenRouter). Скрипт печатает метод +
    # контекст; Burry pre-IPO оценку (EV на выходе + IRR + margin of safety)
    # прогоняет аналитик в ответе.
    bar = "=" * 72
    print(f"{bar}\nBURRY PRE-IPO ОЦЕНКА — {name}\n"
          "Аналитик (Claude Code): по методу ниже дай Bear/Base/Bull EV на выходе,\n"
          "IRR от входа и вердикт margin of safety.\n"
          f"{bar}\n=== МЕТОД (system_burry) ===\n{system_prompt}\n\n"
          f"=== ЗАДАЧА (task_preipo) ===\n{task}\n\n"
          f"=== КОНТЕКСТ КОМПАНИИ ({name}) ===\n{context}")


if __name__ == "__main__":
    main()
