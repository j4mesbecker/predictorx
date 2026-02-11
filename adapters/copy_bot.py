"""
Adapter for polymarket-copy-bot repo.
Imports: WhaleTracker, curated whales, sentiment analytics.
Source: /Users/jamesbecker/Desktop/polymarket-copy-bot/src/
"""

import importlib
import logging

logger = logging.getLogger(__name__)


def get_curated_whales():
    """Get the curated whale list with tiers."""
    try:
        mod = importlib.import_module("whales.curated")
        if hasattr(mod, "CURATED_WHALES"):
            return mod.CURATED_WHALES
        if hasattr(mod, "CuratedWhales"):
            return mod.CuratedWhales
        return mod
    except ImportError as e:
        logger.warning(f"Could not import curated whales: {e}")
        return {}


def get_whale_tracker():
    """Get the WhaleTracker class."""
    try:
        mod = importlib.import_module("whales.tracker")
        if hasattr(mod, "WhaleTracker"):
            return mod.WhaleTracker
        return mod
    except ImportError as e:
        logger.warning(f"Could not import whale tracker: {e}")
        return None


def get_analytics_dashboard():
    """Get the analytics dashboard for sentiment analysis."""
    try:
        mod = importlib.import_module("analytics.dashboard")
        if hasattr(mod, "AnalyticsDashboard"):
            return mod.AnalyticsDashboard
        return mod
    except ImportError as e:
        logger.warning(f"Could not import analytics dashboard: {e}")
        return None


def get_leaderboard():
    """Get the leaderboard scraper."""
    try:
        mod = importlib.import_module("discovery.leaderboard")
        return mod
    except ImportError as e:
        logger.warning(f"Could not import leaderboard: {e}")
        return None
