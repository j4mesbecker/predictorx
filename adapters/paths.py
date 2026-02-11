"""
PredictorX — Repo Import Bridge
Adds all existing repo source directories to sys.path so their modules are importable.

IMPORTANT: Repo paths are APPENDED (not inserted at 0) so our own project's
packages (config, core, etc.) always take priority over identically-named
modules in the source repos.
"""

import sys
from pathlib import Path

# Our project root — MUST always be first on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
    """Add repo source directories to sys.path (appended, not prepended).

    Our project root is always kept at position 0 so that our own
    config/, core/, etc. packages are never shadowed by identically-named
    modules in the source repos.
    """
    project_root = str(_PROJECT_ROOT)

    # Ensure our project root is first
    if project_root in sys.path:
        sys.path.remove(project_root)
    sys.path.insert(0, project_root)

    # Append repo paths (after our project)
    for name, path in REPO_PATHS.items():
        str_path = str(path)
        if path.exists() and str_path not in sys.path:
            sys.path.append(str_path)


def verify_paths() -> dict[str, bool]:
    """Check which repo paths exist. Returns dict of name -> exists."""
    return {name: path.exists() for name, path in REPO_PATHS.items()}
