"""
PredictorX — Scheduled Telegram Alerts
Only actionable messages. No noise.

Three alert types:
  1. PRE-MARKET SCAN (6:30 AM CST / 7:30 AM ET) — regime, watchlist, trade plan
  2. TRADE EXECUTION — real-time "BUY NOW" with exact contract (from spx_monitor)
  3. EXIT / CUT LOSS — take profit or bail signals

All times in CST (user is in Madison, WI).
"""

import logging
from datetime import date, datetime, timedelta

from telegram.bot import get_bot
from telegram.formatters import TOS, KAL
from config.constants import (
    BLACKOUT_DATES, MONTHLY_RISK_FACTOR, DOW_DROP2_RATE,
    SAFE_MONTHS, RISKY_MONTHS,
    PSYCH_HOLD_WINNER, PSYCH_CUT_LOSER, PSYCH_NO_REVENGE,
    PSYCH_SIZE_CHECK, PSYCH_SYSTEM_TRUST,
)

logger = logging.getLogger(__name__)

# ── Position Tracking (for exit alerts) ─────────────────────
# Tracks what the bot has suggested buying so it can suggest exits
_open_positions: list[dict] = []


def track_position(ticker: str, strike: str, entry_price: float,
                   spy_at_entry: float, target_pct: float = 50.0,
                   stop_pct: float = -50.0, position_type: str = "long_call",
                   entry_premium: float = 0.0):
    """
    Track a suggested position for exit monitoring.

    position_type: "long_call", "naked_put", "naked_call"
    entry_premium: for naked options, the credit received (for exit calculations)
    """
    _open_positions.append({
        "ticker": ticker,
        "strike": strike,
        "entry_price": entry_price,
        "spy_at_entry": spy_at_entry,
        "target_pct": target_pct,
        "stop_pct": stop_pct,
        "opened": datetime.now(),
        "position_type": position_type,
        "entry_premium": entry_premium,
        "entry_day_of_week": datetime.now().strftime("%A"),
    })


async def pre_market_scan():
    """
    6:30 AM CST (7:30 AM ET) — Pre-market briefing.
    Sends: regime, key events, watchlist with specific contracts, trade plan.
    """
    bot = get_bot()
    if not bot.configured:
        return

    try:
        from adapters.kalshi_data import get_vix, get_spx
        vix_data = get_vix()
        spx_data = get_spx()
    except Exception as e:
        logger.error(f"Pre-market scan fetch failed: {e}")
        return

    vix_price = vix_data.get("price", 0)
    regime = vix_data.get("regime", "MEDIUM")
    spx_price = spx_data.get("price", 0)
    spy_price = spx_price / 10 if spx_price else 0
    prev_close = spx_data.get("prev_close", 0)

    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    day_name = today.strftime("%A")
    month = today.month
    dow = today.weekday()

    is_blackout = today_str in BLACKOUT_DATES
    month_factor = MONTHLY_RISK_FACTOR.get(month, 1.0)
    dow_rate = DOW_DROP2_RATE.get(dow, 0.043)

    # Overnight move
    overnight_pct = ((spx_price - prev_close) / prev_close * 100) if prev_close else 0

    # Dynamic expiry: min 14 DTE
    min_exp = today + timedelta(days=14)
    days_to_friday = (4 - min_exp.weekday()) % 7
    exp_date = min_exp + timedelta(days=days_to_friday)
    exp_str = exp_date.strftime("%b %d")

    # Build call watchlist at current levels
    atm_spy = round(spy_price)
    qqq_est = spx_price * 0.0883 if spx_price else 0
    atm_qqq = round(qqq_est)

    lines = []

    # ── Header ────────────────────────────────────────────────
    lines.append(f"<b>PRE-MARKET</b> | {day_name} {today.strftime('%b %d')}")
    lines.append(
        f"SPX {spx_price:,.0f} ({overnight_pct:+.2f}% overnight)"
        f" | VIX {vix_price:.1f} ({regime})"
    )
    lines.append("")

    # ── Events / Warnings ─────────────────────────────────────
    if is_blackout:
        lines.append("<b>EVENT DAY</b> — FOMC/CPI/NFP")
        lines.append("Tail trades BLOCKED. Dip buy + VIX reversion active.")
        lines.append("")

    if month in RISKY_MONTHS:
        lines.append(f"Month risk: {month_factor:.2f}x (elevated)")
    if dow in (0, 4):  # Mon/Fri
        lines.append(f"{day_name}: {dow_rate:.1%} chance of >2% drop (above avg)")
    lines.append("")

    # ── Trade Plan ────────────────────────────────────────────
    if regime in ("LOW", "LOW_MED"):
        lines.append("<b>PLAN: Buy calls on dips</b>")
        lines.append(f"Regime {regime} = 98% bounce rate on >1% dips")
        if overnight_pct < -0.5:
            lines.append("Overnight gap down — watch for entry at open")
        else:
            lines.append("Wait for intraday dip. Alerts armed at -1%, -1.5%.")
    elif regime == "MEDIUM":
        lines.append("<b>PLAN: Selective only</b>")
        lines.append("Regime MEDIUM — only buy calls after >1.5% dips")
        lines.append("Watch for VIX reversion signal")
    else:
        lines.append("<b>PLAN: CASH or puts</b>")
        lines.append(f"Regime {regime} — do not buy calls")
    lines.append("")

    # ── Watchlist ─────────────────────────────────────────────
    lines.append(f"<b>WATCHLIST</b> ({exp_str} exp, 14+ DTE)")
    lines.append("")

    if regime in ("LOW", "LOW_MED", "MEDIUM"):
        lines.append("<b>Calls (buy on dip):</b>")
        lines.append(f"{TOS} SPY {atm_spy}C — ATM")
        lines.append(f"{TOS} SPY {atm_spy - 3}C — ITM (safer)")
        lines.append(f"{TOS} SPY {atm_spy + 3}C — OTM (cheaper)")
        lines.append(f"{TOS} QQQ {atm_qqq}C — ATM (higher beta)")
        lines.append(f"{TOS} QQQ {atm_qqq + 5}C — OTM")
    else:
        lines.append("<b>Puts (hedge / directional):</b>")
        lines.append(f"{TOS} SPY {atm_spy}P — ATM")
        lines.append(f"{TOS} QQQ {atm_qqq}P — ATM")

    lines.append("")
    lines.append("Options window: 8:30 AM - 3:30 PM CST")
    lines.append("Best entry: 8:30-9:30 AM CST (first hour after open)")
    lines.append("Alerts will fire if thresholds hit.")

    text = "\n".join(lines)
    await bot.send_via_friday(text)
    logger.info("Pre-market scan sent")


async def check_exit_signals():
    """
    Runs every 5 min during market hours.
    Checks if SPY has moved enough from entry to trigger
    take-profit or cut-loss on tracked positions.
    """
    if not _open_positions:
        return

    bot = get_bot()
    if not bot.configured:
        return

    try:
        from adapters.kalshi_data import get_spx
        spx_data = get_spx()
        spx_price = spx_data.get("price", 0)
        spy_price = spx_price / 10 if spx_price else 0
    except Exception:
        return

    if not spy_price:
        return

    positions_to_remove = []

    now = datetime.now()
    day_of_week = now.strftime("%A")

    for i, pos in enumerate(_open_positions):
        spy_entry = pos["spy_at_entry"]
        spy_move_pct = ((spy_price - spy_entry) / spy_entry) * 100
        position_type = pos.get("position_type", "long_call")

        # ── Naked Put / Naked Call exit logic ──────────────────
        if position_type in ("naked_put", "naked_call"):
            days_held = (now - pos["opened"]).days
            entry_premium = pos.get("entry_premium", 0)

            # Time exit: Wednesday for positions entered Mon/Tue (weekly theta)
            if day_of_week == "Wednesday" and days_held >= 1:
                lines = [
                    f"{TOS} <b>TIME EXIT — CLOSE {position_type.upper().replace('_', ' ')}</b>",
                    "",
                    f"Position: {pos['ticker']} {pos['strike']}",
                    f"Entered: {pos['opened'].strftime('%b %d %I:%M %p')} CST ({days_held}d ago)",
                    "",
                    "Wednesday time exit rule. Buy to close now.",
                    "Don't hold weeklies into Thursday/Friday theta crush.",
                    "",
                    f"<i>{PSYCH_SYSTEM_TRUST}</i>",
                ]
                await bot.send_via_friday("\n".join(lines))
                positions_to_remove.append(i)
                continue

            if position_type == "naked_put":
                # Take profit proxy: SPY rallied enough that put premium likely at 50% target
                # SPY up +0.8% from entry = put premium roughly halved
                if spy_move_pct >= 0.8:
                    lines = [
                        f"{TOS} <b>TAKE PROFIT — NAKED PUT</b>",
                        "",
                        f"SPY rallied +{spy_move_pct:.1f}% from entry ({spy_entry:.0f} \u2192 {spy_price:.0f})",
                        f"Position: {pos['ticker']} {pos['strike']}",
                        f"Entry premium: ~${entry_premium:.2f}" if entry_premium else "",
                        "",
                        "Put premium likely at or below 50% target. Buy to close.",
                        f"<i>{PSYCH_HOLD_WINNER}</i>" if spy_move_pct < 1.2 else "Take profit NOW.",
                    ]
                    await bot.send_via_friday("\n".join([l for l in lines if l]))
                    positions_to_remove.append(i)

                # Cut loss proxy: SPY dropped -1.5% from entry = put premium likely doubled
                elif spy_move_pct <= -1.5:
                    lines = [
                        f"{TOS} <b>CUT LOSS — NAKED PUT</b>",
                        "",
                        f"SPY dropped {spy_move_pct:.1f}% from entry ({spy_entry:.0f} \u2192 {spy_price:.0f})",
                        f"Position: {pos['ticker']} {pos['strike']}",
                        "",
                        "Put premium likely doubled (2x stop). Buy to close NOW.",
                        f"<i>{PSYCH_CUT_LOSER}</i>",
                        "",
                        f"<i>{PSYCH_NO_REVENGE}</i>",
                    ]
                    await bot.send_via_friday("\n".join(lines))
                    positions_to_remove.append(i)

                # Warning: SPY down >0.8% = put getting expensive
                elif spy_move_pct <= -0.8 and not pos.get("warned"):
                    lines = [
                        f"{TOS} <b>NAKED PUT WARNING</b>",
                        "",
                        f"SPY down {spy_move_pct:.1f}% — put premium rising",
                        f"Position: {pos['ticker']} {pos['strike']}",
                        "",
                        "Buy to close if SPY drops to -1.5% from entry.",
                        f"<i>{PSYCH_SIZE_CHECK}</i>",
                    ]
                    await bot.send_via_friday("\n".join(lines))
                    pos["warned"] = True

            elif position_type == "naked_call":
                # Take profit proxy: SPY dropped = call premium decaying
                if spy_move_pct <= -0.8:
                    lines = [
                        f"{TOS} <b>TAKE PROFIT — NAKED CALL</b>",
                        "",
                        f"SPY dropped {spy_move_pct:.1f}% from entry ({spy_entry:.0f} \u2192 {spy_price:.0f})",
                        f"Position: {pos['ticker']} {pos['strike']}",
                        "",
                        "Call premium likely at 50% target. Buy to close.",
                    ]
                    await bot.send_via_friday("\n".join(lines))
                    positions_to_remove.append(i)

                # Cut loss proxy: SPY rallied +1.5% = call premium doubled
                elif spy_move_pct >= 1.5:
                    lines = [
                        f"{TOS} <b>CUT LOSS — NAKED CALL</b>",
                        "",
                        f"SPY rallied +{spy_move_pct:.1f}% from entry ({spy_entry:.0f} \u2192 {spy_price:.0f})",
                        f"Position: {pos['ticker']} {pos['strike']}",
                        "",
                        "Call premium likely doubled (2x stop). Buy to close NOW.",
                        f"<i>{PSYCH_CUT_LOSER}</i>",
                    ]
                    await bot.send_via_friday("\n".join(lines))
                    positions_to_remove.append(i)

                elif spy_move_pct >= 0.8 and not pos.get("warned"):
                    lines = [
                        f"{TOS} <b>NAKED CALL WARNING</b>",
                        "",
                        f"SPY up +{spy_move_pct:.1f}% — call premium rising",
                        f"Position: {pos['ticker']} {pos['strike']}",
                        "",
                        "Buy to close if SPY rallies to +1.5% from entry.",
                    ]
                    await bot.send_via_friday("\n".join(lines))
                    pos["warned"] = True

            continue  # Skip the long_call logic below

        # ── Long call exit logic (existing) ────────────────────
        # Take profit: SPY recovered +1.5% from entry (calls ~double)
        if spy_move_pct >= 1.5:
            lines = [
                "<b>TAKE PROFIT</b>",
                "",
                f"SPY moved +{spy_move_pct:.1f}% from entry ({spy_entry:.0f} to {spy_price:.0f})",
                f"Position: {pos['ticker']} {pos['strike']}",
                "",
                "Sell half now, let rest ride.",
                f"Entered: {pos['opened'].strftime('%b %d %I:%M %p')} CST",
            ]
            await bot.send_via_friday("\n".join(lines))
            positions_to_remove.append(i)

        # Cut loss: SPY dropped -1.5% from entry (calls down ~50-70%)
        elif spy_move_pct <= -1.5:
            lines = [
                "<b>CUT LOSS</b>",
                "",
                f"SPY dropped {spy_move_pct:.1f}% from entry ({spy_entry:.0f} to {spy_price:.0f})",
                f"Position: {pos['ticker']} {pos['strike']}",
                "",
                "Exit now. Preserve capital.",
                "Wait for VIX reversion signal before re-entry.",
            ]
            await bot.send_via_friday("\n".join(lines))
            positions_to_remove.append(i)

        # Warning: down >0.8% from entry
        elif spy_move_pct <= -0.8:
            # Only warn once (check if already warned)
            if not pos.get("warned"):
                lines = [
                    "<b>POSITION WARNING</b>",
                    "",
                    f"SPY down {spy_move_pct:.1f}% from entry",
                    f"Position: {pos['ticker']} {pos['strike']}",
                    "",
                    "Tighten stop. Cut at -1.5% from entry.",
                ]
                await bot.send_via_friday("\n".join(lines))
                pos["warned"] = True

    # Remove closed positions
    for i in sorted(positions_to_remove, reverse=True):
        _open_positions.pop(i)


async def evening_prep():
    """
    4:30 PM CST (5:30 PM ET) — Evening prep for next trading day.
    Tomorrow's plan, key events, overnight watchlist.
    """
    bot = get_bot()
    if not bot.configured:
        return

    try:
        from adapters.kalshi_data import get_vix, get_spx
        vix_data = get_vix()
        spx_data = get_spx()
    except Exception:
        return

    vix_price = vix_data.get("price", 0)
    regime = vix_data.get("regime", "MEDIUM")
    spx_price = spx_data.get("price", 0)

    tomorrow = date.today() + timedelta(days=1)
    # Skip weekends
    if tomorrow.weekday() >= 5:
        tomorrow = tomorrow + timedelta(days=(7 - tomorrow.weekday()))

    tomorrow_str = tomorrow.strftime("%Y-%m-%d")
    is_blackout = tomorrow_str in BLACKOUT_DATES
    tomorrow_name = tomorrow.strftime("%A %b %d")

    lines = []
    lines.append(f"<b>EVENING PREP</b> | Tomorrow: {tomorrow_name}")
    lines.append(f"SPX {spx_price:,.0f} | VIX {vix_price:.1f} ({regime})")
    lines.append("")

    if is_blackout:
        lines.append("<b>TOMORROW IS EVENT DAY</b> (FOMC/CPI/NFP)")
        lines.append("Expect volatility. VIX likely to spike pre-market.")
        lines.append("Plan: wait for dip after data release, buy calls 8:30-9:30 CST")
    else:
        lines.append("No major events tomorrow.")
        if regime in ("LOW", "LOW_MED"):
            lines.append("Continue dip-buying strategy if opportunities arise.")
        elif regime == "MEDIUM":
            lines.append("Selective mode — only high-conviction entries.")

    if _open_positions:
        lines.append("")
        lines.append(f"<b>Open positions: {len(_open_positions)}</b>")
        for pos in _open_positions:
            lines.append(f"  {pos['ticker']} {pos['strike']} (from {pos['opened'].strftime('%b %d')})")

    lines.append("")
    lines.append("Pre-market scan at 6:30 AM CST.")

    await bot.send_via_friday("\n".join(lines))
    logger.info("Evening prep sent")


def register_actionable_alerts(scheduler):
    """
    Register ONLY actionable alert jobs.
    Replaces the old generic morning_scan / daily_summary.
    """
    from apscheduler.triggers.cron import CronTrigger

    # Pre-market scan — 6:30 AM CST = 7:30 AM ET
    scheduler.add_job(
        pre_market_scan,
        CronTrigger(hour=7, minute=30, day_of_week="mon-fri", timezone="US/Eastern"),
        id="pre_market_scan",
        name="Pre-Market Scan (6:30 AM CST)",
        replace_existing=True,
    )

    # Exit signal checker — every 5 min during options trading hours
    # 8:30 AM - 3:30 PM CST = 9:30 AM - 4:30 PM ET
    scheduler.add_job(
        check_exit_signals,
        CronTrigger(
            hour="9-16", minute="*/5", day_of_week="mon-fri", timezone="US/Eastern"
        ),
        id="exit_signals",
        name="Exit Signal Check (5min)",
        replace_existing=True,
    )

    # Evening prep — 4:30 PM CST = 5:30 PM ET
    scheduler.add_job(
        evening_prep,
        CronTrigger(hour=17, minute=30, day_of_week="mon-fri", timezone="US/Eastern"),
        id="evening_prep",
        name="Evening Prep (4:30 PM CST)",
        replace_existing=True,
    )

    logger.info("Registered 3 actionable Telegram alert jobs")
