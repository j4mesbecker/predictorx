"""
Adapter for prediction-market-analysis-main repo.
Imports: Statistical analysis tools, Brier score, ECE, calibration tests.
Source: /Users/jamesbecker/Desktop/prediction-market-analysis-main/src/
"""

import importlib
import logging

logger = logging.getLogger(__name__)


def get_analysis_base():
    """Get the Analysis base class."""
    try:
        return importlib.import_module("common.analysis")
    except ImportError as e:
        logger.warning(f"Could not import analysis base: {e}")
        return None


def get_kalshi_indexer():
    """Get the Kalshi trade indexer."""
    try:
        return importlib.import_module("indexers.kalshi.trades")
    except ImportError as e:
        logger.warning(f"Could not import Kalshi indexer: {e}")
        return None


def get_polymarket_indexer():
    """Get the Polymarket trade indexer."""
    try:
        return importlib.import_module("indexers.polymarket.trades")
    except ImportError as e:
        logger.warning(f"Could not import Polymarket indexer: {e}")
        return None
