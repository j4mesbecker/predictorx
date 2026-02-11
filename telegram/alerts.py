"""
PredictorX — Compact Alert System
Color-coded: 🔵 ThinkorSwim | 🟢 Kalshi
"""

import logging
from core.models import Prediction, Opportunity, VixSnapshot
from telegram.bot import get_bot
from telegram.formatters import format_opportunity, TOS, KAL

logger = logging.getLogger(__name__)


async def send_opportunity_alert(opportunity: Opportunity):
    """Compact high-confidence opportunity alert."""
    bot = get_bot()
    if not bot.configured:
        return

    text = f"<b>TRADE</b>\n\n{format_opportunity(opportunity)}"
    await bot.send_message(text)
    logger.info(f"Sent alert: {opportunity.prediction.market_ticker}")


async def send_regime_change_alert(old_regime: str, new_regime: str, vix: VixSnapshot):
    """One-line regime change alert."""
    bot = get_bot()
    if not bot.configured:
        return

    action = {
        "LOW": "Full deploy",
        "LOW_MED": ">3% and >5% tails only",
        "MEDIUM": ">5% tails only",
        "HIGH": "No tails — weather/arb",
        "CRISIS": "ALL CASH",
    }

    spx = f" | SPX {vix.spx_price:,.0f}" if vix.spx_price else ""
    text = (
        f"<b>VIX REGIME</b>  {old_regime} \u2192 <b>{new_regime}</b>"
        f"  |  VIX {vix.price:.1f}{spx}\n"
        f"{action.get(new_regime, 'Monitor')}"
    )
    await bot.send_message(text)
    logger.info(f"Regime: {old_regime} -> {new_regime}")


async def send_whale_alert(wallet_alias: str, category: str, market: str,
                           side: str, amount: float):
    """Compact whale alert."""
    bot = get_bot()
    if not bot.configured:
        return

    text = (
        f"<b>WHALE</b>  {wallet_alias} ({category})\n"
        f"{side} ${amount:,.0f} — {market}"
    )
    await bot.send_message(text)
    logger.info(f"Whale: {wallet_alias} {side} ${amount:,.0f}")


async def send_daily_summary(opportunities: list[Opportunity], perf: dict, vix: VixSnapshot = None):
    """Compact end-of-day summary."""
    bot = get_bot()
    if not bot.configured:
        return

    total = perf.get("total_predictions", 0)
    accuracy = perf.get("accuracy", 0)
    pnl = perf.get("total_pnl", 0)

    vix_str = f"  |  VIX {vix.price:.1f} ({vix.regime})" if vix else ""
    lines = [
        f"<b>EOD</b>{vix_str}",
        f"{total} trades | {accuracy:.0%} acc | ${pnl:+.2f}",
    ]

    if opportunities:
        lines.append("")
        lines.append("<b>Tomorrow:</b>")
        for opp in opportunities[:3]:
            lines.append(format_opportunity(opp))

    await bot.send_message("\n".join(lines))
    logger.info("Sent daily summary")
