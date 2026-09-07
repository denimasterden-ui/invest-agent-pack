import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORK_AREA = PROJECT_ROOT.parent

# ── Модели ───────────────────────────────────────────────────────────────────
# Все вызовы идут через OpenRouter, поэтому вендор не зашит: любую роль можно
# перевести на другую семью, не трогая код. Переопределяется переменной
# окружения (её же выставляет селектор моделей в дашборде).
#
# Супервизор намеренно ДРУГОЙ семьи, чем аналитик: самопроверка одной моделью
# не работает — в прогоне CHTR 24.07 модель подтвердила собственную ложную
# ссылку на свежесть данных. Нужны некоррелированные ошибки, а не второй
# проход тем же взглядом. См. dev/DECISIONS.md, D3-D4.

MODEL_RESEARCH   = os.environ.get("INVEST_MODEL_RESEARCH",   "perplexity/sonar")
MODEL_EXTRACT    = os.environ.get("INVEST_MODEL_EXTRACT",    "z-ai/glm-5.2")

# Аналитик и супервизор выбираются в дашборде, поэтому читаются функцией, а не
# константой: константа замерзает на импорте, а Streamlit держит импортированные
# модули между реранами — выбор в селекторе не доехал бы до прогона, который
# идёт в том же процессе. Файл (а не только os.environ) нужен, чтобы выбор
# пережил перезапуск дашборда и был виден CLI-прогонам.
#
# Приоритет: переменная окружения → сохранённый выбор → дефолт. Env выигрывает
# намеренно: разовый запуск с явной моделью не должен молча слушаться того, что
# кто-то выбрал в UI неделю назад.
MODEL_CHOICE_FILE = PROJECT_ROOT / ".models.json"

DEFAULT_MODEL_ANALYST    = "anthropic/claude-sonnet-4-6"
DEFAULT_MODEL_SUPERVISOR = "openai/gpt-5.4"


def _saved_models() -> dict:
    try:
        saved = json.loads(MODEL_CHOICE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return saved if isinstance(saved, dict) else {}


def model_analyst() -> str:
    if provider() == PROVIDER_CODEX:
        return (os.environ.get("INVEST_MODEL_ANALYST")
                or _saved_models().get("codex_analyst")
                or DEFAULT_CODEX_MODEL_ANALYST)
    return (os.environ.get("INVEST_MODEL_ANALYST")
            or _saved_models().get("analyst") or DEFAULT_MODEL_ANALYST)


def model_supervisor() -> str:
    if provider() == PROVIDER_CODEX:
        return (os.environ.get("INVEST_MODEL_SUPERVISOR")
                or _saved_models().get("codex_supervisor")
                or DEFAULT_CODEX_MODEL_SUPERVISOR)
    return (os.environ.get("INVEST_MODEL_SUPERVISOR")
            or _saved_models().get("supervisor") or DEFAULT_MODEL_SUPERVISOR)


def save_models(analyst: str, supervisor: str) -> Path:
    """Сохранить выбор моделей. Действует на следующий прогон, без перезапуска.

    Ключи — по активному провайдеру: селектор при codex показывает модели
    подписки, их же он и сохраняет. Выбор другого провайдера и его модели
    сохраняются в соседних ключах — переключение туда-обратно ничего не теряет.
    """
    if not analyst or not supervisor:
        raise ValueError("модель не может быть пустой")
    saved = _saved_models()
    if provider() == PROVIDER_CODEX:
        saved["codex_analyst"], saved["codex_supervisor"] = analyst, supervisor
    else:
        saved["analyst"], saved["supervisor"] = analyst, supervisor
    MODEL_CHOICE_FILE.write_text(
        json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return MODEL_CHOICE_FILE

# Что предлагать в селекторе дашборда. Список — подсказка, а не ограничение:
# в env можно передать любой идентификатор модели OpenRouter.
MODEL_CHOICES = [
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-opus-4-8",
    "openai/gpt-5.4",
    "google/gemini-2.5-pro",
    "deepseek/deepseek-v4-pro",
    "z-ai/glm-5.2",
]

# ── Провайдер LLM: OpenRouter или подписка Codex (#42) ───────────────────────
# Оба провайдера живут за одним швом — llm.chat(): вызывающие роли
# (update_scenarios, supervise, run_method) передают модель и не знают, через
# что идёт вызов. Переключение — выбором в сайдбаре, без правки кода.
#
# Персист — тот же паттерн, что у моделей: env → сохранённый выбор → дефолт.
# Ключи моделей каждого провайдера лежат рядом в том же .models.json
# (analyst/supervisor для OpenRouter, codex_analyst/codex_supervisor для
# подписки), поэтому переключение туда-обратно не теряет ни один выбор.

PROVIDER_OPENROUTER = "openrouter"
PROVIDER_CODEX     = "codex"
PROVIDER_CHOICES   = [PROVIDER_OPENROUTER, PROVIDER_CODEX]
DEFAULT_PROVIDER   = PROVIDER_OPENROUTER

# Модели подписки ChatGPT (из ~/.codex/models_cache.json) — альтернатива
# MODEL_CHOICES на время, пока активен провайдер codex. Тоже подсказка, а не
# ограничение: env принимает любой идентификатор модели подписки.
CODEX_MODEL_CHOICES = [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
]

# Супервизор — ДРУГОЕ поколение, чем аналитик: принцип D3-D4 (некоррелированные
# ошибки) внутри одной подписки позволяет максимум различий между поколениями;
# полноценная «другая семья», как у OpenRouter-пары, тут недостижима.
DEFAULT_CODEX_MODEL_ANALYST    = "gpt-5.6-sol"
DEFAULT_CODEX_MODEL_SUPERVISOR = "gpt-5.5"

# Роли, чьи модели заданы КОНСТАНТАМИ OpenRouter (research/extract), при
# провайдере codex переводятся на модели подписки: идентификаторы вида
# «perplexity/sonar» подпиской не существуют, а update_scenarios.py передаёт
# именно константы — его контракт менять нельзя. Подбор — самая дешёвая
# подходящая: extract — узкая задача с жёстким форматом, research — бриф.
CODEX_ROLE_MODELS = {
    MODEL_RESEARCH: "gpt-5.5",
    MODEL_EXTRACT:  "gpt-5.4",
}


def provider() -> str:
    """Активный провайдер LLM: env → сохранённый выбор → дефолт.

    Читается функцией, а не замораживается на импорте — тот же паттерн, что у
    model_analyst(): Streamlit держит модуль между реранами, выбор из
    сайдбара должен доезжать до прогонов в том же процессе.
    """
    val = (os.environ.get("INVEST_LLM_PROVIDER")
           or _saved_models().get("provider"))
    return val if val in PROVIDER_CHOICES else DEFAULT_PROVIDER


def save_provider(value: str) -> Path:
    """Сохранить выбор провайдера. Действует на следующий вызов llm.chat()."""
    if value not in PROVIDER_CHOICES:
        raise ValueError(f"неизвестный провайдер: {value}")
    saved = _saved_models()
    saved["provider"] = value
    MODEL_CHOICE_FILE.write_text(
        json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return MODEL_CHOICE_FILE


# Выбор материалов галочками «В Спеку» (#45, критерий «сохраняется между
# перезапуском»): тот же файл и паттерн приоритетов, что у моделей/провайдера.
# Сохраняется список ВКЛЮЧЁННЫХ имён по тикеру: пустой список — человек снял
# всё, отсутствие записи — выбора ещё не было (действует дефолт «всё
# разобранное включено»).
def material_selection(ticker: str):
    saved = _saved_models().get("material_selection")
    if not isinstance(saved, dict):
        return None
    names = saved.get(str(ticker or "").strip().upper())
    return list(names) if isinstance(names, list) else None


def save_material_selection(ticker: str, names: list) -> Path:
    if not str(ticker or "").strip():
        raise ValueError("тикер не может быть пустым")
    saved = _saved_models()
    selection = saved.get("material_selection")
    if not isinstance(selection, dict):
        selection = {}
    selection[str(ticker).strip().upper()] = list(names)
    saved["material_selection"] = selection
    MODEL_CHOICE_FILE.write_text(
        json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return MODEL_CHOICE_FILE


def model_choices() -> list:
    """Список-подсказка для селектора дашборда по активному провайдеру."""
    return list(CODEX_MODEL_CHOICES if provider() == PROVIDER_CODEX
                else MODEL_CHOICES)


def codex_model_for(model: str) -> str:
    """Модель подписки для запрошенной роли.

    Голое имя подписки (gpt-…) проходит как есть — список моделей подписки
    подсказка, а не ограничение, и env-пин модели вне пятёрки должен
    работать. Идентификатор OpenRouter (со слешем) подпиской не существует:
    константа известной роли (CODEX_ROLE_MODELS) переводится на модель
    подписки, прочий — на дефолт аналитика, иначе `codex exec -m` упал бы на
    первом же вызове.
    """
    if "/" not in model:
        return model
    return CODEX_ROLE_MODELS.get(model, DEFAULT_CODEX_MODEL_ANALYST)


GSHEET_SA_KEY = WORK_AREA / "sinizza-1169-301dd9ddc8ae.json"

SPREADSHEET_ID = "1CGDrjj15WjimrQ8oOBfbj0AGG6h-9Enl9dTebamu0dM"
SHEET_FUNDAMENTAL  = "Фундаментал"
SHEET_HISTORY      = "scenarios_history"
SHEET_IDEAS        = "invest_ideas"

IDEA_COLUMNS = [
    "date_added",     # когда добавили идею
    "ticker",
    "company",
    "currency",
    "current_price",  # цена на момент добавления
    "bear_low", "bear_high",
    "base_low", "base_high",
    "bull_low", "bull_high",
    "timeline_months",  # сколько месяцев до Base по оценке
    "status",           # watching | active | passed | rejected
    "key_catalyst",     # главный катализатор (кратко)
    "key_risk",         # главный риск (кратко)
    "analyst_note",     # полный текст анализа
    "scenario_id",      # постоянная ссылка на выбранный сценарий
    "scenario_label",
    "scenario_thesis",  # снимок тезиса на момент сборки идеи
    "return_low_pct", "return_mid_pct", "return_high_pct",
    "annual_return_low_pct", "annual_return_mid_pct", "annual_return_high_pct",
    "needs_rebuild",    # пометка поверх жизненного статуса идеи
    "rebuild_reason",
]

HISTORY_COLUMNS = [
    "timestamp",
    "ticker",
    "currency",
    "current_price",
    "bear_low", "bear_high",
    "base_low", "base_high",
    "bull_low", "bull_high",
    "trigger",
    "trigger_note",
    "full_text",
]
