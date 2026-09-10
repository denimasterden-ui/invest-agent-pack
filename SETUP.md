# SETUP — invest-agent pack

## 1. Python + зависимости
Нужен Python **3.12+** (свой venv, не системный):
```bash
python3.12 -m venv ~/.venvs/invest312
~/.venvs/invest312/bin/pip install -r requirements.txt
```
Зависимости: `yfinance` (факты/peers), `mcp` (канон), `py_vollib` (опционы
entry-timing), `beautifulsoup4`+`lxml` (разбор human-source `.mhtml`).
**LLM-ключ не нужен** — ресёрч и генерацию делает Claude/субагент, код только
валидирует форму, сверяет числа с baseline и считает коридоры.

## 2. Окружение канона
Прогоны копятся в центральном каноне. Задай в профиле оболочки (`~/.zshrc`):
```bash
export INVEST_PY="$HOME/.venvs/invest312/bin/python"
export INVEST_MCP_URL="https://ai.sinizzais.ru/investagent/mcp"  # публичный канон (TLS)
export INVEST_AUTHOR="твоё_имя"                     # атрибуция в каноне (обязательно)
export INVEST_TOKEN="<твой_токен>"                  # выдаёт владелец канона
```

> **Доступ.** Канон закрыт токеном (`Authorization: Bearer`). Токен = твоя
> личность в каноне: прогоны подписываются твоим автором, чужим именем не
> подписаться. Без валидного `INVEST_TOKEN` — 401. Токен запроси у владельца
> канона; ни SSH, ни туннель не нужны — только эти три переменные.

## 3. Проверка
```bash
$INVEST_PY invest-dashboard/agent/run_evals.py   # должно быть зелёно (кроме options, если нет py_vollib)
export INVEST_AUTHOR=test
./invest-dashboard/agent-run/ia show             # роестр канона (или фолбэк)
```

## 4. Первый прогон
В Claude Code открой этот репо, вызови `/ask-denis` — гид объяснит метод и
проведёт первый прогон. Затем `/investagent-run ТИКЕР`.

Метод в двух строках: собери инфу (факты + ресёрч субагентом по
`knowledge/research_method.md` + свой `.mhtml`-материал) → прогони бумагу через
**12 призм** → осознанно выбери меру → Base мерой из призм → тезисы форками.
Гейты останавливают расчёт до числа и называют, что дособрать — **отказ это
результат**.
