#!/usr/bin/env python3
"""Regression eval: a debt-heavy bear fork records a zero equity floor."""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import fork, measures, profile  # noqa: E402


ASSUMPTIONS = {
    "revenue_base": 100.0,
    "shares": 10.0,
    "net_cash": -100.0,
    "scenarios": {"base": {
        "growth_path": [0.20] * 5,
        "discount_rate": 0.10,
        "terminal_fcf_margin": 0.30,
        "terminal_fcf_multiple": 20.0,
    }},
}


def override(value):
    return fork.Override(
        value=value,
        rationale="bear-маржа оставляет стоимость бизнеса ниже долга",
        anchor_class=fork.COMPANY_GUIDE,
        source="bear-case guidance",
        confirms=fork.DRIVER,
    )


@patch("agent.fork.store_client.record_fork")
@patch("agent.measures.profile.build")
def main(profile_build, record_fork):
    profile_build.return_value = profile.Profile(
        ticker="CRWV",
        research_type="ai_infra",
        business_kind="аренда вычислительной мощности",
        measure=profile.EV_REVENUE,
        measure_reason="долговой неоклауд оценивается через EV/Revenue",
        drivers=(),
        caps=(),
        comps=("NBIS",),
        multiple_metric="ev_per_sales",
        driver_facts={},
    )
    base_result = measures.calculate("CRWV", ASSUMPTIONS)
    assert base_result.corridor[0] > 0, base_result.corridor
    base = fork.basis(base_result, price=10.0, price_currency="USD")
    bear = fork.Fork(
        label=fork.BEAR,
        thesis="маржа сжимается, и enterprise value не покрывает долг",
        channel=fork.CASH_FLOW,
        channel_reason="тезис меняет терминальную маржу потока",
        horizon_months=24,
        must_be_true=("терминальная маржа остаётся около 1%",),
        overrides={fork.TERMINAL_FCF_MARGIN: override(0.01)},
    )

    result = fork.evaluate(base, bear)

    assert result.corridor == (0, 0), result.corridor
    assert result.expected == {
        "low": (-1.0, -1.0),
        "high": (-1.0, -1.0),
        "mid": (-1.0, -1.0),
        "years": 2.0,
    }, result.expected
    record_fork.assert_called_once()
    assert record_fork.call_args.args[3] == (0, 0)
    print("PASS: debt-heavy bear fork is calculated and recorded at zero equity")


if __name__ == "__main__":
    main()
