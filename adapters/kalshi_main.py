"""
Adapter for kalshi (main) repo.
Imports: RSA-signed Kalshi API client, 7-gate position sizer.
Source: /Users/jamesbecker/Desktop/kalshi/src/
"""

import importlib
import logging

logger = logging.getLogger(__name__)


def get_kalshi_client_module():
    """Get the Kalshi client module with RSA-PSS signing."""
    try:
        return importlib.import_module("kalshi.client")
    except ImportError:
        try:
            return importlib.import_module("src.kalshi.client")
        except ImportError as e:
            logger.warning(f"Could not import Kalshi client: {e}")
            return None


def get_position_sizer():
    """Get the 7-gate position sizer."""
    try:
        return importlib.import_module("trading.sizing")
    except ImportError:
        try:
            return importlib.import_module("src.trading.sizing")
        except ImportError as e:
            logger.warning(f"Could not import position sizer: {e}")
            return None


def get_trade_executor():
    """Get the trade executor (for reference — predictions only, no execution)."""
    try:
        return importlib.import_module("trading.executor")
    except ImportError:
        try:
            return importlib.import_module("src.trading.executor")
        except ImportError as e:
            logger.warning(f"Could not import trade executor: {e}")
            return None
