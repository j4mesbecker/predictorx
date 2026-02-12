"""
PredictorX — Telegram Scheduler (DEPRECATED)
Old generic alerts replaced by telegram/scheduled_alerts.py.

Only actionable alerts now:
  1. Pre-market scan (6:30 AM CST)
  2. Trade execution (real-time from spx_monitor)
  3. Exit / cut-loss signals

This file is kept for backwards compatibility only.
Use telegram.scheduled_alerts.register_actionable_alerts() instead.
"""

import logging

logger = logging.getLogger(__name__)


def register_scheduled_tasks(scheduler):
    """
    DEPRECATED — replaced by telegram.scheduled_alerts.register_actionable_alerts().
    This is a no-op kept for backwards compatibility.
    """
    logger.info("telegram.scheduler.register_scheduled_tasks() is deprecated — "
                "using telegram.scheduled_alerts.register_actionable_alerts() instead")
