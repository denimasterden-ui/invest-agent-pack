#!/usr/bin/env python3
"""SPC-023: fork relies only on a live security and supported terminal facts."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import fork, profile  # noqa: E402


def baseline_result(ticker):
    assumptions = {
        "revenue_base": 1_000.0,
        "shares": 100.0,
        "net_cash": 0.0,
        "scenarios": {"base": {
            "growth_path": [.4, .3, .2, .15, .1],
            "discount_rate": .1,
            "terminal_fcf_margin": .05,
            "terminal_fcf_multiple": 15.0,
        }},
    }
    return SimpleNamespace(
        ticker=ticker, measure=profile.EV_REVENUE,
        measure_reason="revenue and terminal cash flow",
        price=20.0, price_currency="USD", corridor=(10.0, 12.0),
        detail=assumptions, is_refusal=lambda: False,
    )


def branch(margin, anchor=fork.OWN_HISTORY, confirms=fork.NUMBER):
    def override(value, selected_anchor=anchor, selected_confirms=confirms):
        return fork.Override(value, "scenario", selected_anchor,
                             "reported facts", selected_confirms)

    return fork.Fork(
        label=fork.BULL, thesis="scale improves cash flow",
        channel=fork.CASH_FLOW, channel_reason="cash flow changes",
        horizon_months=24, must_be_true=("margin improves",), overrides={
            fork.GROWTH_PATH: override([.45, .35, .25, .18, .12],
                                       fork.COMPANY_GUIDE, fork.DRIVER),
            fork.TERMINAL_FCF_MARGIN: override(margin),
        })


def main():
    with patch("agent.kernel.store_client._symbol_facts", return_value={}):
        try:
            fork.basis(baseline_result("SPCX"))
        except ValueError as error:
            assert "живой биржевой символ" in str(error), error
        else:
            raise AssertionError("unconfirmed SPCX reached fork calculation")

    facts = {
        "quoteType": "EQUITY", "symbol": "NEWCO1", "currency": "USD",
        "historicalFcfMargins": [-.42, -.31, -.18],
    }
    calculated = SimpleNamespace(corridor=(24.0, 28.0), is_refusal=lambda: False)
    with patch("agent.kernel.store_client._symbol_facts", return_value=facts), \
            patch("agent.kernel.store_client.record_fork"), \
            patch("agent.fork.measures.calculate", return_value=calculated):
        base = fork.basis(baseline_result("NEWCO1"))
        unsupported = fork.evaluate(base, branch(.24))
        assert not unsupported.is_refusal()
        assert unsupported.corridor is not None
        assert not unsupported.bettable
        assert unsupported.expected is None
        assert fork.TERMINAL_FCF_MARGIN in unsupported.unanchored

        supported = fork.evaluate(base, branch(-.25))
        assert not supported.is_refusal()
        assert supported.bettable
        assert supported.expected is not None
        assert fork.TERMINAL_FCF_MARGIN not in supported.unanchored

    # Курированный тикер: провайдерских фактов у него нет по построению
    # (symbol_facts коротко замыкается на реестре). Отсутствие ряда НЕ должно
    # объявлять маржу неподпёртой — иначе по всем знакомым бумагам ставка
    # перестаёт считаться. Ряд объявляет вызывающий.
    with patch("agent.kernel.store_client.record_fork"), \
            patch("agent.fork.measures.calculate", return_value=calculated):
        known = fork.basis(baseline_result("MU"))
        assert known.historical_fcf_margins == ()
        blind = fork.evaluate(known, branch(.24))
        assert blind.bettable, "без ряда судить не о чем — ставка должна считаться"
        assert blind.expected is not None
        assert fork.TERMINAL_FCF_MARGIN not in blind.unanchored

        told = fork.basis(baseline_result("MU"),
                          historical_fcf_margins=(-.42, -.31, -.18))
        judged = fork.evaluate(told, branch(.24))
        assert not judged.bettable, "объявленный ряд не покрывает 24% — наблюдение"
        assert judged.expected is None
        assert fork.TERMINAL_FCF_MARGIN in judged.unanchored

    print("Все PASS — SPC-023")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
