"""API-key lookup shared by the v2 entry points."""

import os
from pathlib import Path


ENV_FILE = Path.home() / "Documents" / "Claude" / "Projects" / "aiops" / ".env"


def get_api_key() -> str:
    """Return the project gateway key, falling back to the OpenRouter key."""
    names = ("INVEST_LLM_KEY", "OPENROUTER_API_KEY")
    for name in names:
        key = os.environ.get(name)
        if key:
            return key
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()
        for name in names:
            for line in lines:
                if line.startswith(f"{name}="):
                    return line.split("=", 1)[1].strip()
    return ""
