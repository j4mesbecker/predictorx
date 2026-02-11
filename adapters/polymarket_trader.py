"""
Adapter for polymarket-trader repo.
Imports: WeatherAnalyzer, CalibrationEngine, Kelly functions, edge finder utilities.
Source: /Users/jamesbecker/Desktop/polymarket-trader/src/
"""

import importlib
import logging

logger = logging.getLogger(__name__)

# Lazy imports to handle missing dependencies gracefully
_weather_analyzer = None
_calibration_engine = None


def get_weather_analyzer():
    """Get the WeatherAnalyzer class from polymarket-trader."""
    global _weather_analyzer
    if _weather_analyzer is None:
        try:
            mod = importlib.import_module("weather_analyzer")
            _weather_analyzer = mod
            logger.info("Loaded WeatherAnalyzer from polymarket-trader")
        except ImportError as e:
            logger.warning(f"Could not import weather_analyzer: {e}")
            return None
    return _weather_analyzer


def get_calibration_engine():
    """Get the CalibrationEngine from polymarket-trader."""
    global _calibration_engine
    if _calibration_engine is None:
        try:
            mod = importlib.import_module("calibration_engine")
            _calibration_engine = mod
            logger.info("Loaded CalibrationEngine from polymarket-trader")
        except ImportError as e:
            logger.warning(f"Could not import calibration_engine: {e}")
            return None
    return _calibration_engine


def get_kalshi_stations():
    """Get KALSHI_STATIONS config from weather_analyzer."""
    mod = get_weather_analyzer()
    if mod and hasattr(mod, "KALSHI_STATIONS"):
        return mod.KALSHI_STATIONS
    # Fallback to our own constants
    from config.constants import KALSHI_STATIONS
    return KALSHI_STATIONS


def get_edge_finder():
    """Get the edge finder module."""
    try:
        return importlib.import_module("kalshi_edge_finder")
    except ImportError as e:
        logger.warning(f"Could not import kalshi_edge_finder: {e}")
        return None
