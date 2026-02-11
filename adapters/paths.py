"""
PredictorX — Repo Import Bridge
Adds all existing repo source directories to sys.path so their modules are importable.
"""

import sys
from pathlib import Path

# Base directory for all repos
_BASE = Path("/Users/jamesbecker/Desktop")

REPO_PATHS = {
    "polymarket_trader_src": _BASE / "polymarket-trader" / "src",
    "polymarket_trader_root": _BASE / "polymarket-trader",
    "kalshi_data_root": _BASE / "kalshi_data",
    "kalshi_data_bot": _BASE / "kalshi_data" / "bot",
    "kalshi_main_root": _BASE / "kalshi",
    "kalshi_main_src": _BASE / "kalshi" / "src",
    "prediction_analysis": _BASE / "prediction-market-analysis-main",
    "prediction_analysis_src": _BASE / "prediction-market-analysis-main" / "src",
    "copy_bot_root": _BASE / "polymarket-copy-bot",
    "copy_bot_src": _BASE / "polymarket-copy-bot" / "src",
}


def setup_paths():
    """Add all repo source directories to sys.path for importing."""
    for name, path in REPO_PATHS.items():
        str_path = str(path)
        if path.exists() and str_path not in sys.path:
            sys.path.insert(0, str_path)


def verify_paths() -> dict[str, bool]:
    """Check which repo paths exist. Returns dict of name -> exists."""
    return {name: path.exists() for name, path in REPO_PATHS.items()}
