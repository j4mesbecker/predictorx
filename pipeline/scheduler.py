"""
PredictorX — APScheduler Pipeline
Manages all scheduled data fetching, prediction generation, and settlement tasks.
"""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from pipeline.tasks import (
    fetch_weather_forecasts,
    fetch_vix_data,
    fetch_whale_activity,
    generate_predictions,
    settle_predictions,
    daily_performance_snapshot,
    update_calibration,
    generate_tos_daily_intel,
)
from pipeline.spx_monitor import check_spx_price
from pipeline.stock_monitor import check_stock_levels
from pipeline.spx_bracket_scanner import scan_spx_brackets
from telegram.scheduled_alerts import register_actionable_alerts

logger = logging.getLogger(__name__)


def create_scheduler() -> AsyncIOScheduler:
    """Create and configure the APScheduler with all pipeline jobs."""
    scheduler = AsyncIOScheduler(timezone="US/Eastern")

    # ── Data Fetching ──────────────────────────────────────

    # Weather forecasts — every 3 hours (aggressive: fresher data = better edge)
    scheduler.add_job(
        fetch_weather_forecasts,
        IntervalTrigger(hours=3),
        id="fetch_weather",
        name="Fetch Weather Forecasts (4 sources, 3hr)",
        replace_existing=True,
    )

    # VIX level + regime — every 15 min during market hours
    scheduler.add_job(
        fetch_vix_data,
        CronTrigger(
            hour="9-16", minute="*/15", day_of_week="mon-fri",
        ),
        id="fetch_vix",
        name="Fetch VIX Data",
        replace_existing=True,
    )

    # Whale activity — every 10 minutes 24/7
    scheduler.add_job(
        fetch_whale_activity,
        IntervalTrigger(minutes=10),
        id="fetch_whales",
        name="Fetch Whale Activity",
        replace_existing=True,
    )

    # ── SPX Real-Time Monitor ────────────────────────────────

    # SPX price monitor — every 5 min during market hours
    # Detects intraday drops and fires reactive trade alerts
    # Backed by 6,563-day backtest with clustering + regime gates
    scheduler.add_job(
        check_spx_price,
        CronTrigger(
            hour="9-16", minute="*/5", day_of_week="mon-fri",
        ),
        id="spx_monitor",
        name="SPX Drop Monitor (5min)",
        replace_existing=True,
    )

    # ── Stock Level Monitor (TSLA, NVDA) ─────────────────────

    # Individual stock level monitor — every 2 min during market hours
    # Tracks key support/resistance/breakout levels and fires alerts
    scheduler.add_job(
        check_stock_levels,
        CronTrigger(
            hour="9-16", minute="*/2", day_of_week="mon-fri",
        ),
        id="stock_monitor",
        name="Stock Level Monitor (TSLA/NVDA, 2min)",
        replace_existing=True,
    )

    # ── SPX Bracket Scanner (Kalshi INX) ──────────────────────

    # SPX bracket scanner — every 30 min during market hours
    # Scans Kalshi for brackets in the 10-49c YES sweet spot (94.7% NO WR)
    # On catalyst days (CPI/FOMC/NFP): runs every 15 min starting 7:45 AM ET
    # Backed by 10,000-market analysis
    scheduler.add_job(
        scan_spx_brackets,
        CronTrigger(
            hour="8-16", minute="5,20,35,50", day_of_week="mon-fri",
        ),
        id="spx_bracket_scan",
        name="SPX Bracket Scanner (15min)",
        replace_existing=True,
    )

    # ── TOS Daily Intelligence Report ───────────────────────

    # Morning pre-market TOS intel — 7:15 AM ET (6:15 AM CST) weekdays
    # Read-only intelligence for ThinkOrSwim: SPX levels, dip-buy calls,
    # put credit spreads, catalyst calendar, VIX reversion status
    scheduler.add_job(
        generate_tos_daily_intel,
        CronTrigger(hour=7, minute=15, day_of_week="mon-fri"),
        id="tos_daily_intel",
        name="TOS Daily Intelligence (6:15 AM CST)",
        replace_existing=True,
    )

    # ── Prediction Generation ──────────────────────────────

    # Generate predictions — every 15 min during trading hours (aggressive mode)
    scheduler.add_job(
        generate_predictions,
        CronTrigger(
            hour="6-18", minute="*/15", day_of_week="mon-fri",
        ),
        id="generate_predictions",
        name="Generate Predictions (15min)",
        replace_existing=True,
    )

    # ── Settlement ─────────────────────────────────────────

    # Settle predictions — hourly 24/7
    scheduler.add_job(
        settle_predictions,
        IntervalTrigger(hours=1),
        id="settle_predictions",
        name="Settle Predictions",
        replace_existing=True,
    )

    # ── Performance & Calibration ──────────────────────────

    # Daily performance snapshot — 5:30 PM ET weekdays
    scheduler.add_job(
        daily_performance_snapshot,
        CronTrigger(hour=17, minute=30, day_of_week="mon-fri"),
        id="daily_performance",
        name="Daily Performance Snapshot",
        replace_existing=True,
    )

    # Calibration update — midnight daily
    scheduler.add_job(
        update_calibration,
        CronTrigger(hour=0, minute=0),
        id="calibration_update",
        name="Calibration Update",
        replace_existing=True,
    )

    # ── Telegram Actionable Alerts ─────────────────────────
    # Only 3 alert types: pre-market scan, trade execution, exit/cut-loss
    # (replaces old 6-job generic alert system)

    register_actionable_alerts(scheduler)

    logger.info(f"Scheduler configured with {len(scheduler.get_jobs())} jobs")
    return scheduler
