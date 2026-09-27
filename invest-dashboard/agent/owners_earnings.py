"""Tragic Algebra — денежная цена SBC и доля прибыли, принадлежащая владельцу.

GAAP списывает SBC по оценке гранта. Настоящая цена возникает годами позже:
акции вестятся, счёт акций растёт, компания выкупает бумаги по сегодняшней
цене и платит деньгами налог за сотрудника. Модуль считает эту цену и
отношение прибыли владельца к отчётной — ΔE.

Стандарт и ловушки входов — knowledge/iv15_standard.md, часть 1. Здесь только
арифметика: входы объявляет тот, кто прочитал 10-K, модуль их не добывает.
"""
from __future__ import annotations

from dataclasses import dataclass

# Пул NDX-97: 97 компаний, 1017 годовых 10-K, earnings-weighted. Заглушка для
# бумаги, по которой 10-K ещё не прочитаны, — не факт о бумаге.
POOL_DELTA_E = 0.834


@dataclass(frozen=True)
class Period:
    """Год из 10-K. Все величины — в валюте отчётности, акции — в штуках."""
    year: int
    net_income: float        # N
    gaap_sbc: float          # G — расход SBC по GAAP
    shares_start: float      # S₀
    shares_end: float        # S₁
    buyback_dollars: float   # T — потрачено по одобренной программе
    buyback_shares: float    # W — выкуплено штук, БЕЗ удержанных под налог
    withholding_net: float   # C = Cw − Ce
    avg_price: float | None = None  # нужна только когда программы выкупа нет


def sbc_cost(period: Period) -> float:
    """Σ = V + C — денежная цена SBC за период."""
    delta_s = period.shares_end - period.shares_start
    issued = delta_s + period.buyback_shares            # I
    if period.buyback_shares:
        price = period.buyback_dollars / period.buyback_shares   # P = T/W
    elif period.avg_price:
        price = period.avg_price
    else:
        raise ValueError(
            f"{period.year}: программы выкупа нет (W=0), нужна avg_price — "
            "средняя рыночная цена за год вместо P=T/W")
    return issued * price + period.withholding_net


def owners_earnings(period: Period) -> float:
    """OE = N + G − Σ. G возвращается, чтобы уступить место денежной цене."""
    return period.net_income + period.gaap_sbc - sbc_cost(period)


def delta_e(periods) -> dict:
    """Пуловая, взвешенная прибылью ΔE: сумма OE к сумме N за все периоды.

    Взвешивание прибылью, а не усреднение по годам: прибыль и есть механизм
    создания стоимости, и она же переживает смену режимов SBC внутри декады.
    """
    periods = list(periods)
    if not periods:
        raise ValueError("нужен хотя бы один период")
    total_oe = sum(owners_earnings(p) for p in periods)
    total_n = sum(p.net_income for p in periods)
    if total_n <= 0:
        raise ValueError("суммарная прибыль не положительна — ΔE не определена")
    years = len(periods)
    return {
        "delta_e": total_oe / total_n,
        "owners_earnings": total_oe,
        "net_income": total_n,
        "years": years,
        "shallow": years < 10,   # декада — глубина стандарта
    }
