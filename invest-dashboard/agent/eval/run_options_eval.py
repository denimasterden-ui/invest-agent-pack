#!/usr/bin/env python3
"""Offline behavior eval for the options kernel (SPC-013 K2)."""
import builtins
import sys
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.kernel.options import _iv  # noqa: E402


def main():
    # Black-Scholes call fixture: S=K=100, T=1, r=4.5%, sigma=20%.
    iv = _iv(10.18611055482976, 100.0, 100.0, 1.0, "c")
    assert iv is not None and abs(iv - 0.20) < 1e-9, iv

    real_import = builtins.__import__

    def without_vollib(name, *args, **kwargs):
        if name == "py_vollib" or name.startswith("py_vollib."):
            raise ImportError("py_vollib deliberately unavailable")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=without_vollib):
        assert _iv(10.0, 100.0, 100.0, 1.0, "c") is None

    print("options eval: PASS")


if __name__ == "__main__":
    main()
