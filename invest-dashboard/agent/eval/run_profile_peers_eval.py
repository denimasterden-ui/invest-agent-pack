#!/usr/bin/env python3
"""Offline behavior eval: profile shows live peer multiples."""
import contextlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent import cli  # noqa: E402


class StubTicker:
    DATA = {
        "MU": {"forwardPE": 8.5},
        "WDC": {"forwardPE": 12.75},
        "STX": {"forwardPE": 10.25},
    }

    def __init__(self, symbol):
        self.symbol = symbol

    @property
    def info(self):
        return self.DATA.get(self.symbol, {})


class StubYFinance:
    Ticker = StubTicker


class UnavailableTicker(StubTicker):
    @property
    def info(self):
        if self.symbol == "STX":
            raise RuntimeError("market data unavailable")
        return super().info


class UnavailableYFinance:
    Ticker = UnavailableTicker


def render(yf_module):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli.cmd_profile(SimpleNamespace(ticker="MU"),
                               yf_module=yf_module)
    return code, output.getvalue()


def main():
    code, rendered = render(StubYFinance)
    unavailable_code, unavailable = render(UnavailableYFinance)
    checks = {
        "command succeeds with live peer values": code == 0,
        "WDC forward_pe is shown": "WDC 12.75" in rendered,
        "STX forward_pe is shown": "STX 10.25" in rendered,
        "declared unit remains visible": "в единицах forward_pe" in rendered,
        "command succeeds when one peer is unavailable": unavailable_code == 0,
        "unavailable STX is marked": "STX (н/д)" in unavailable,
    }
    failed = []
    for description, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}: {description}")
        if not passed:
            failed.append(description)
    if failed:
        print(rendered)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
