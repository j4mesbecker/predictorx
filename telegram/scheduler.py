"""
PredictorX — Telegram Scheduled Alerts
Morning scan, market-open tails, daily summary, and event-driven alerts.
"""

import logging
from datetime import datetime

from core.registry import StrategyRegistry
from core.models import VixSnapshot, Opportunity
from telegram.bot import get_bot
from telegram.alerts import (
    send_opportunity_alert,
    send_regime_change_alert,
    send_daily_summary,
    send_whale_alert,
)
from telegram.formatters import format_morning_scan, format_tail_analysis
from config.settings import get_settings

logger = logging.getLogger(__name__)

# Track last known VIX regime for change detection
_last_regime: str | None = None
_registry: StrategyRegistry | None = None


def _get_registry() -> StrategyRegistry:
    global _registry
    if _registry is None:
        _registry = StrategyRegistry()
    return _registry


async def morning_scan():
    """
    6:00 AM ET — Run all strategies and send top opportunities.
    """
    bot = get_bot()
    if not bot.configured:
        return

    logger.info("Running morning scan...")
    try:
        registry = _get_registry()
        settings = get_settings()
        opportunities = await registry.scan_all(balance=settings.starting_capital)

        vix = _fetch_vix()

        if not opportunities:
            logger.info("Morning scan: no opportunities, skipping Telegram")
            return

        text = format_morning_scan(opportunities, vix)
        await bot.send_message(text)

        # Only alert on exceptional opportunities (edge >= 12% AND confidence >= 90%)
        for opp in opportunities[:3]:
            if opp.prediction.confidence_score >= 0.90 and opp.prediction.edge >= 0.12:
                await send_opportunity_alert(opp)

        logger.info(f"Morning scan complete: {len(opportunities)} opportunities")

    except Exception as e:
        logger.error(f"Morning scan error: {e}")
        await bot.send_message(f"Morning scan failed: {e}")


async def market_open_tails():
    """
    9:30 AM ET — S&P tail opportunities at market open.
    """
    bot = get_bot()
    if not bot.configured:
        return

    logger.info("Running market-open tail scan...")
    try:
        registry = _get_registry()
        settings = get_settings()
        predictions = await registry.scan_strategy("sp_tail", balance=settings.starting_capital)

        vix = _fetch_vix()

        text = format_tail_analysis(predictions, vix)
        await bot.send_message(text)

        logger.info(f"Market-open tail scan: {len(predictions)} predictions")

    except Exception as e:
        logger.error(f"Market-open tail scan error: {e}")


async def daily_summary_task():
    """
    5:00 PM ET — End-of-day summary with performance and next-day picks.
    """
    bot = get_bot()
    if not bot.configured:
        return

    logger.info("Generating daily summary...")
    try:
        # Get performance data
        perf = _get_daily_performance()

        # Get tomorrow's opportunities
        registry = _get_registry()
        settings = get_settings()
        opportunities = await registry.scan_all(balance=settings.starting_capital)

        vix = _fetch_vix()

        await send_daily_summary(opportunities, perf, vix)
        logger.info("Daily summary sent")

    except Exception as e:
        logger.error(f"Daily summary error: {e}")


async def check_regime_change():
    """
    Event-driven — Check for VIX regime changes every 15 minutes.
    Sends alert if regime transitions.
    """
    global _last_regime

    bot = get_bot()
    if not bot.configured:
        return

    try:
        vix = _fetch_vix()
        if vix is None:
            return

        current_regime = vix.regime

        if _last_regime is None:
            _last_regime = current_regime
            return

        if current_regime != _last_regime:
            logger.warning(f"VIX regime change: {_last_regime} -> {current_regime}")
            await send_regime_change_alert(_last_regime, current_regime, vix)
            _last_regime = current_regime
        else:
            _last_regime = current_regime

    except Exception as e:
        logger.error(f"Regime check error: {e}")


async def check_high_confidence_alerts():
    """
    Event-driven — Only alert on exceptional trades worth taking.
    Confidence >= 92% AND edge >= 15%. Runs every 2 hours during market hours.
    """
    bot = get_bot()
    if not bot.configured:
        return

    try:
        registry = _get_registry()
        settings = get_settings()
        opportunities = await registry.scan_all(balance=settings.starting_capital)

        for opp in opportunities:
            p = opp.prediction
            if p.confidence_score >= 0.92 and p.edge >= 0.15:
                await send_opportunity_alert(opp)
                logger.info(
                    f"High-value alert: {p.market_ticker} "
                    f"({p.confidence_score:.0%}, +{p.edge:.0%} edge)"
                )

    except Exception as e:
        logger.error(f"High-confidence check error: {e}")


async def check_whale_activity():
    """
    Event-driven — Only alert on massive LEGEND whale moves (>$25k).
    Runs every hour. Most whale intel goes to the web dashboard silently.
    """
    bot = get_bot()
    if not bot.configured:
        return

    try:
        from adapters.copy_bot import get_curated_whales
        whales = get_curated_whales()

        if not isinstance(whales, dict):
            return

        for addr, info in whales.items():
            if not isinstance(info, dict):
                continue
            category = info.get("category", "")
            if category == "LEGEND":
                recent_trades = info.get("recent_trades", [])
                for trade in recent_trades:
                    amount = trade.get("amount", 0)
                    if amount >= 25000:  # Only massive moves
                        await send_whale_alert(
                            wallet_alias=info.get("alias", addr[:8]),
                            category=category,
                            market=trade.get("market", "Unknown"),
                            side=trade.get("side", "?"),
                            amount=amount,
                        )

    except Exception as e:
        logger.error(f"Whale activity check error: {e}")


def _fetch_vix() -> VixSnapshot | None:
    """Fetch current VIX data, return None on failure."""
    try:
        from adapters.kalshi_data import get_vix, get_spx
        vix_data = get_vix()
        spx_data = get_spx()
        return VixSnapshot(
            price=vix_data["price"],
            regime=vix_data["regime"],
            spx_price=spx_data.get("price"),
        )
    except Exception:
        return None


def _get_daily_performance() -> dict:
    """Fetch daily performance from database."""
    try:
        from db.repository import Repository
        settings = get_settings()
        repo = Repository(settings.database_sync_url)
        return repo.get_performance_summary(days=1)
    except Exception as e:
        logger.error(f"Performance fetch error: {e}")
        return {"total_predictions": 0, "accuracy": 0, "total_pnl": 0}


def register_scheduled_tasks(scheduler):
    """
    Register all PredictorX scheduled tasks with APScheduler.
    Called from pipeline/scheduler.py during startup.
    """
    from apscheduler.triggers.cron import CronTrigger

    # Morning scan — 6:00 AM ET weekdays
    scheduler.add_job(
        morning_scan,
        CronTrigger(hour=6, minute=0, day_of_week="mon-fri", timezone="US/Eastern"),
        id="morning_scan",
        name="PredictorX Morning Scan",
        replace_existing=True,
    )

    # Market-open tail scan — 9:30 AM ET weekdays
    scheduler.add_job(
        market_open_tails,
        CronTrigger(hour=9, minute=30, day_of_week="mon-fri", timezone="US/Eastern"),
        id="market_open_tails",
        name="Market Open Tail Scan",
        replace_existing=True,
    )

    # VIX regime check — every 15 min during market hours
    scheduler.add_job(
        check_regime_change,
        CronTrigger(
            hour="9-16", minute="*/15", day_of_week="mon-fri", timezone="US/Eastern"
        ),
        id="regime_check",
        name="VIX Regime Check",
        replace_existing=True,
    )

    # High-value alerts — every 2 hours during trading hours (only >92% conf, >15% edge)
    scheduler.add_job(
        check_high_confidence_alerts,
        CronTrigger(
            hour="6,8,10,12,14,16", minute=0, day_of_week="mon-fri", timezone="US/Eastern"
        ),
        id="high_confidence_check",
        name="High Value Alert Check",
        replace_existing=True,
    )

    # Whale activity — hourly, only LEGEND >$25k moves
    scheduler.add_job(
        check_whale_activity,
        "interval",
        hours=1,
        id="whale_check",
        name="Whale Activity Check",
        replace_existing=True,
    )

    # Daily summary — 5:00 PM ET weekdays
    scheduler.add_job(
        daily_summary_task,
        CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone="US/Eastern"),
        id="daily_summary",
        name="Daily Summary",
        replace_existing=True,
    )

    logger.info("Registered 6 scheduled Telegram tasks")
