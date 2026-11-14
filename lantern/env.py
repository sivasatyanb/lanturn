"""Load local configuration from .env (git-ignored). Values are never printed or logged."""
from __future__ import annotations

from .config import ROOT

_loaded = False


def load_env() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env", override=False)
