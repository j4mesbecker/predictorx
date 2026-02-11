"""
Adapter for kalshi_data repo.
Imports: VIX fetcher, signal generation, tail probabilities.
Source: /Users/jamesbecker/Desktop/kalshi_data/
"""

import importlib
import logging
import sys

logger = logging.getLogger(__name__)

_vix_module = None
_signals_module = None


def get_vix_module():
    """Get the VIX module from kalshi_data/bot/vix.py."""
    global _vix_module
    if _vix_module is None:
        try:
            # The module is at kalshi_data/bot/vix.py
            # Since kalshi_data/bot is on sys.path, import directly
            _vix_module = importlib.import_module("vix")
            logger.info("Loaded VIX module from kalshi_data")
        except ImportError as e:
            logger.warning(f"Could not import vix module: {e}")
            return None
    return _vix_module


def get_vix() -> dict:
    """Fetch current VIX data. Returns {"price": float, "regime": str, "source": str}."""
    mod = get_vix_module()
    if mod:
        return mod.get_vix()
    raise RuntimeError("VIX module not available")


def get_spx() -> dict:
    """Fetch current S&P 500 data."""
    mod = get_vix_module()
    if mod:
        return mod.get_spx()
    raise RuntimeError("VIX module not available")


def get_tail_prob() -> dict:
    """Get the TAIL_PROB table (historical probabilities by VIX regime)."""
    mod = get_vix_module()
    if mod and hasattr(mod, "TAIL_PROB"):
        return mod.TAIL_PROB
    # Fallback to constants
    from config.constants import TAIL_PROB
    return TAIL_PROB


def tail_probability(regime: str, pct_drop: float) -> float:
    """Get historical probability of a >X% drop given VIX regime."""
    mod = get_vix_module()
    if mod:
        return mod.tail_probability(regime, pct_drop)
    # Fallback
    from config.constants import TAIL_PROB
    probs = TAIL_PROB.get(regime, TAIL_PROB["MEDIUM"])
    return probs.get(int(pct_drop), 0.05)


def compute_tail_strikes(spx_price: float) -> list[dict]:
    """Compute S&P price levels for tail drop thresholds."""
    mod = get_vix_module()
    if mod:
        return mod.compute_tail_strikes(spx_price)
    # Fallback
    strikes = []
    for pct in [1.0, 2.0, 3.0, 5.0, 7.0]:
        level = round(spx_price * (1 - pct / 100), 2)
        strikes.append({"pct": pct, "strike": level,
                        "label": f">{pct}% drop below {spx_price:.0f}"})
    return strikes


def generate_signals() -> dict:
    """Generate trading signals based on current VIX regime."""
    try:
        # signals.py imports config and vix as siblings
        mod = importlib.import_module("signals")
        return mod.generate_signals()
    except ImportError as e:
        logger.warning(f"Could not import signals module: {e}")
        return {
            "timestamp": None, "vix": None, "spx": None,
            "regime": "UNKNOWN", "budget": 0,
            "blocked": True, "block_reason": f"Signal module unavailable: {e}",
            "signals": [], "summary": "Signal generation unavailable",
        }
