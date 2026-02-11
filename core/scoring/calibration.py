"""
PredictorX — Calibration Wrapper
Wraps the polymarket-trader calibration engine for historical accuracy corrections.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Cache the calibration data
_calibration_data: Optional[dict] = None


def load_calibration() -> dict:
    """Load calibration data from the polymarket-trader repo."""
    global _calibration_data
    if _calibration_data is not None:
        return _calibration_data

    # Try loading from the original repo
    cal_path = Path("/Users/jamesbecker/Desktop/polymarket-trader/calibration.json")
    if cal_path.exists():
        try:
            with open(cal_path) as f:
                _calibration_data = json.load(f)
            logger.info(f"Loaded calibration data from {cal_path}")
            return _calibration_data
        except Exception as e:
            logger.warning(f"Failed to load calibration.json: {e}")

    # Fallback: basic calibration curve
    _calibration_data = {
        "price_to_actual": {
            "0.05": 0.0, "0.10": 0.05, "0.15": 0.10, "0.20": 0.15,
            "0.25": 0.20, "0.30": 0.28, "0.35": 0.33, "0.40": 0.38,
            "0.45": 0.44, "0.50": 0.50, "0.55": 0.56, "0.60": 0.62,
            "0.65": 0.67, "0.70": 0.72, "0.75": 0.78, "0.80": 0.82,
            "0.85": 0.88, "0.90": 0.93, "0.95": 1.00,
        },
        "city_bias": {},
        "total_markets_analyzed": 0,
    }
    return _calibration_data


def calibrate_probability(raw_prob: float, strategy: str = "weather") -> float:
    """Apply calibration correction to a raw probability."""
    cal = load_calibration()
    price_map = cal.get("price_to_actual", {})

    if not price_map:
        return raw_prob

    # Find nearest calibration points
    prices = sorted(float(k) for k in price_map.keys())
    if raw_prob <= prices[0]:
        return float(price_map[str(prices[0])])
    if raw_prob >= prices[-1]:
        return float(price_map[str(prices[-1])])

    # Linear interpolation between nearest points
    for i in range(len(prices) - 1):
        if prices[i] <= raw_prob <= prices[i + 1]:
            lo, hi = prices[i], prices[i + 1]
            lo_val = float(price_map[f"{lo:.2f}"])
            hi_val = float(price_map[f"{hi:.2f}"])
            t = (raw_prob - lo) / (hi - lo)
            return lo_val + t * (hi_val - lo_val)

    return raw_prob


def get_calibration_metrics() -> dict:
    """Get overall calibration metrics (ECE, Brier, etc.)."""
    cal = load_calibration()
    return {
        "total_markets": cal.get("total_markets_analyzed", 0),
        "city_bias": cal.get("city_bias", {}),
        "has_full_data": cal.get("total_markets_analyzed", 0) > 1000,
    }
