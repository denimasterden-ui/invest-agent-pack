"""IV15 — цена, дающая владельцу 15% годовых на горизонте 15 лет.

Не новая мера, а режим: тот же дисконт, что у levered-меры, но ставка
приколочена к 15% и горизонт к 15 годам, а на входе — прибыль владельца
(agent.owners_earnings), а не FCF. На выходе одно число, а не коридор:
ставка задана определением, разбрасывать её чувствительностью нечего.

Стандарт — knowledge/iv15_standard.md. Тир AICT задаёт допущения, из которых
считается IV15 (траекторию роста и терминальный мультипликатор), — сам по
себе множителем к результату он не является.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import owners_earnings
from .measures import TERMINAL_MULTIPLE_CEILING

REQUIRED_RETURN = 0.15
HORIZON = 15

# Веса корзин — НАШ стандарт. Бьюри называет корзины, но весов не публикует.
WEIGHTS = {"shareholder": 0.30, "quality": 0.40, "valuation": 0.30}

# Опора корзины качества: тир AICT. Уровни — из knowledge/classifiers/aict_tiers.md.
TIER_SCORE = {"fortress": 100, "castle": 80, "chapel": 55, "stone": 30, "wood": 5}


@dataclass(frozen=True)
class IV15:
    per_share: float
    ddm: float              # конец A — терминал по Гордону
    multiple: float         # конец B — мультипликатор на OE 15-го года
    confidence_ddm: float   # вес конца A в гибриде
    oe_final: float         # прибыль владельца 15-го года
    warnings: tuple = ()

    def investable(self):
        return self.per_share > 0


def _project(oe_base, growth_path):
    """PV явных лет по ставке 15% и прибыль владельца последнего года."""
    if len(growth_path) != HORIZON:
        raise ValueError(f"growth_path должен быть длиной {HORIZON} лет, "
                         f"получено {len(growth_path)}")
    oe = oe_base
    pv = 0.0
    for year, growth in enumerate(growth_path, 1):
        oe *= 1 + growth
        pv += oe / (1 + REQUIRED_RETURN) ** year
    return pv, oe


def calculate(oe_base, growth_path, terminal_growth, terminal_multiple,
              shares, net_cash=0.0, confidence_ddm=0.5):
    """Гибрид двух концов одной модели; вес — уверенность в каждом.

    net_cash — чистый кэш на балансе; у платёжных компаний из него уже вычтено
    обязательство float (стандарт, часть 4).
    """
    if not shares > 0:
        raise ValueError("shares должны быть положительны")
    if terminal_growth >= REQUIRED_RETURN:
        raise ValueError(f"terminal_growth {terminal_growth:.1%} не ниже "
                         f"требуемой доходности {REQUIRED_RETURN:.0%}")
    if not 0.0 <= confidence_ddm <= 1.0:
        raise ValueError("confidence_ddm — доля конца A, от 0 до 1")

    pv, oe_final = _project(oe_base, growth_path)
    discount = (1 + REQUIRED_RETURN) ** HORIZON
    gordon = oe_final * (1 + terminal_growth) / (REQUIRED_RETURN - terminal_growth)
    ddm = (pv + gordon / discount + net_cash) / shares
    multiple = (pv + terminal_multiple * oe_final / discount + net_cash) / shares

    warnings = []
    if oe_base <= 0:
        warnings.append("прибыль владельца не положительна — IV15 опирается "
                        "на разворот, которого в числах ещё нет")
    spread = abs(ddm - multiple) / max(abs(ddm), abs(multiple), 1e-9)
    if spread > 0.5:
        warnings.append(f"концы модели разошлись на {spread:.0%}: терминал "
                        "несёт слишком много — проверить мультипликатор и "
                        "terminal_growth")

    hybrid = confidence_ddm * ddm + (1 - confidence_ddm) * multiple
    return IV15(hybrid, ddm, multiple, confidence_ddm, oe_final, tuple(warnings))


def terminal_multiple(r_terminal, g_terminal):
    """Мультипликатор 15-го года выводится, а не называется.

    1/(r−g): богатый выход приходится оплачивать явно названным терминальным
    ростом, поэтому нельзя тихо допустить и быстрый рост, и дорогой выход.
    r_terminal — требуемая доходность на ЗРЕЛОМ бизнесе (обычно 9–12%), а не
    входные 15%: 15% — барьер входа, а не терминальная стоимость капитала.
    """
    if r_terminal <= g_terminal:
        raise ValueError("r_terminal должна быть выше g_terminal")
    multiple = 1 / (r_terminal - g_terminal)
    if multiple > TERMINAL_MULTIPLE_CEILING:
        raise ValueError(
            f"мультипликатор {multiple:.1f}× выше потолка "
            f"{TERMINAL_MULTIPLE_CEILING:g}×: пара r={r_terminal:.1%}/"
            f"g={g_terminal:.1%} слишком щедра")
    return multiple


def _clip(value, low, high):
    return max(low, min(high, value))


def shareholder_score(delta_e):
    """ΔE = 1.0 — вся прибыль владельцу; 0.70 — треть ушла мимо."""
    return _clip((delta_e - 0.70) / 0.30 * 100, -50, 100)


def quality_score(tier, roic=None):
    """Тир — опора; ROIC двигает её, потому что рост упирается в ROIC."""
    base = TIER_SCORE.get((tier or "").lower())
    if base is None:
        raise ValueError(f"тир {tier!r} не из словаря AICT: "
                         f"{', '.join(TIER_SCORE)}")
    if roic is None:
        return float(base)
    # ROIC ниже требуемой доходности — рост не окупает вложенный капитал.
    return _clip(base + (roic - REQUIRED_RETURN) * 100, -50, 100)


def valuation_score(p_iv15):
    """1.0× — ровно 15% годовых. Вдвое дороже — минус 55 очков."""
    from math import log2
    if p_iv15 is None:           # отрицательный IV15: цены 15% годовых нет
        return -50.0
    if p_iv15 <= 0:
        raise ValueError("P/IV15 должен быть положителен")
    return _clip(100 - 55 * log2(p_iv15), -50, 120)


def composite(delta_e, tier, p_iv15, roic=None, delta_e_is_pool=False):
    """Итоговый балл относительной оценки внутри сопоставимой группы."""
    buckets = {
        "shareholder": shareholder_score(delta_e),
        "quality": quality_score(tier, roic),
        "valuation": valuation_score(p_iv15),
    }
    score = sum(WEIGHTS[name] * value for name, value in buckets.items())
    notes = []
    if delta_e_is_pool:
        notes.append(f"ΔE пулом ({owners_earnings.POOL_DELTA_E:.3f}), не фактом")
    if p_iv15 is None:
        notes.append("IV15 отрицателен — бумага не инвестируема")
    return {"score": round(score, 1), "buckets": buckets, "notes": tuple(notes)}
