"""AICT — рубрика типизации конкурентной угрозы ИИ (knowledge/classifiers/aict_tiers.md).

Тир — суждение, а не измерение: его признаки («вшит в compliance», «свой ИИ в
масштабе», «модели заимствованы») не лежат в автоисточнике и не выводятся из
отрасли. Поэтому признаки ОБЪЯВЛЯЕТ аналитик, прочитавший отчёт и звонки, а
модуль их детерминированно взвешивает и печатает ЗА/ПРОТИВ по каждому тиру.

Модуль не выбирает тир и не отказывает — как measure_fit для меры. Выбор
объявляется человеком и подтверждается (profile: aict_tier → confirm).
"""
from __future__ import annotations

TIERS = ("fortress", "castle", "chapel", "stone", "wood")

# Признак → как он читается в выводе. None означает «не объявлен».
TRAITS = {
    "compliance_embedded": "вшит в регуляторную/compliance-архитектуру",
    "mission_critical": "необходимая платформа в mission-critical-контуре",
    "owns_ai": "собственный ИИ-IP (куплен или построен)",
    "ai_at_scale": "ИИ развёрнут в настоящем масштабе",
    "ai_aggressor": "сам атакует, а не обороняется",
    "rnd_real": "R&D настоящий и профинансированный",
    "seat_based": "выручка на местах (per-seat)",
    "seat_exposure_large": "seat-база велика или непредсказуема",
    "switching_costs_high": "высокие издержки переключения",
    "moat_shallow": "ров мелкий",
    "peer_pressure_high": "сильное давление конкурентов в отрасли",
    "usage_priced": "перешёл на usage-based прайсинг",
    "acquisition_strategy": "доказанная стратегия поглощений и наблюдения за целями",
    # Единственный признак, разводящий Fortress и Castle: у Castle ров крепок,
    # но как бизнес поведёт себя рядом с ИИ-native игроками — неизвестно.
    "ai_native_uncertainty": "исход рядом с ИИ-native игроками неопределён",
}


def fit(traits: dict) -> list[tuple[str, list[str], list[str]]]:
    """По каждому тиру — ЗА и ПРОТИВ из объявленных признаков."""
    t = {name: traits.get(name) for name in TRAITS}

    def yes(name):
        return t[name] is True

    def no(name):
        return t[name] is False

    rows = []

    za, pr = [], []
    if yes("compliance_embedded"): za.append(TRAITS["compliance_embedded"])
    if yes("mission_critical"): za.append(TRAITS["mission_critical"])
    if yes("ai_aggressor"): za.append(TRAITS["ai_aggressor"])
    if no("ai_native_uncertainty"):
        za.append("реальной угрозы нет ни по замещению, ни по местам")
    if yes("ai_native_uncertainty"):
        pr.append(TRAITS["ai_native_uncertainty"] + " — это уже Castle")
    if yes("seat_exposure_large"): pr.append("seat-база велика — угроза потери мест реальна")
    if no("compliance_embedded") and no("mission_critical"):
        pr.append("не вшит ни в регуляторику, ни в mission-critical-контур")
    rows.append(("fortress", za, pr))

    za, pr = [], []
    if yes("owns_ai") and yes("ai_at_scale"): za.append("свой ИИ в настоящем масштабе")
    if yes("ai_native_uncertainty"): za.append(TRAITS["ai_native_uncertainty"])
    if yes("ai_aggressor"): za.append(TRAITS["ai_aggressor"])
    if yes("seat_based") and no("seat_exposure_large"):
        za.append("seat-бизнес управляемого размера")
    if no("ai_at_scale"): pr.append("масштаб собственного ИИ не объявлен как настоящий")
    if yes("seat_exposure_large"): pr.append("seat-исход непредсказуем — это уже Chapel")
    if yes("moat_shallow"): pr.append("ров мелкий — позиция в отрасли не прочна")
    rows.append(("castle", za, pr))

    za, pr = [], []
    if yes("owns_ai"): za.append("релевантный собственный ИИ-IP есть")
    if yes("rnd_real"): za.append(TRAITS["rnd_real"])
    if yes("switching_costs_high"): za.append(TRAITS["switching_costs_high"])
    if yes("seat_exposure_large"): za.append("острая угроза при живом IP — ровно случай Chapel")
    if no("owns_ai"): pr.append("своего ИИ нет — тир держится на заимствованном")
    if no("rnd_real"): pr.append("R&D недостаточен — ближе к Stone")
    rows.append(("chapel", za, pr))

    za, pr = [], []
    if no("rnd_real"): za.append("R&D сомнителен или недофинансирован")
    if yes("moat_shallow") and yes("peer_pressure_high"):
        za.append("мелкий ров при сильном давлении конкурентов")
    if yes("seat_exposure_large") and no("owns_ai"):
        za.append("угроза есть, адаптироваться нечем")
    if yes("owns_ai") and yes("ai_at_scale"):
        pr.append("свой ИИ в масштабе — способность адаптироваться есть")
    rows.append(("stone", za, pr))

    za, pr = [], []
    if no("owns_ai"): za.append("модели заимствованы, не свои")
    if no("rnd_real"): za.append("внутреннего R&D нет")
    if no("acquisition_strategy"): za.append("доказанной стратегии поглощений нет")
    if yes("owns_ai"): pr.append("ИИ-IP всё же собственный")
    if yes("acquisition_strategy"): pr.append(TRAITS["acquisition_strategy"])
    rows.append(("wood", za, pr))

    return rows


def notes(traits: dict) -> list[str]:
    """Что стоит сказать вслух до выбора тира."""
    out = []
    undeclared = [TRAITS[name] for name in TRAITS if traits.get(name) is None]
    if undeclared:
        out.append("не объявлено: " + "; ".join(undeclared))
    if traits.get("usage_priced") and traits.get("seat_exposure_large"):
        out.append("переход на usage-based отмечен как факт, но seat-риск им не "
                   "закрывается: пока он лишь смазывает измерение")
    return out


def hypothesis(traits: dict) -> str | None:
    """Единственный лидер рубрики — ГИПОТЕЗА для проверки, не решение.

    Ничья гипотезы не даёт: разводить равные тиры порядком в словаре значило
    бы выдать произвол за суждение. Соседние тиры различает признак, которого
    аналитик ещё не объявил, — его и надо объявить.
    """
    scored = [(len(za) - len(pr), tier) for tier, za, pr in fit(traits)]
    top = max(score for score, _ in scored)
    if top <= 0:
        return None
    leaders = [tier for score, tier in scored if score == top]
    return leaders[0] if len(leaders) == 1 else None
