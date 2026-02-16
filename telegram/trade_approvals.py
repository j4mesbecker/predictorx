"""
PredictorX — Trade Approval System
Sends trade opportunities to Telegram with APPROVE / SKIP buttons.
No trades execute without user confirmation.

Flow:
  1. Scanner finds opportunities → calls send_trade_for_approval()
  2. Telegram message sent with inline keyboard [APPROVE] [SKIP]
  3. User taps APPROVE → order placed on Kalshi, confirmation sent
  4. User taps SKIP → message updated, no order placed
  5. Pending trades expire after 30 minutes (prices stale)
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Pending Trade Storage ──────────────────────────────────
# Key: trade_id (str), Value: dict with trade details
_pending_trades: dict[str, dict] = {}

# Trade IDs expire after 30 minutes
TRADE_EXPIRY_SECONDS = 30 * 60


def _generate_trade_id() -> str:
    """Generate a short unique trade ID."""
    return f"t{int(time.time() * 1000) % 1_000_000_000}"


def _format_settlement_time(close_time: str) -> str:
    """Format ISO close_time to human-readable CST time."""
    if not close_time:
        return ""
    try:
        dt = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        # Convert to CST (UTC-6)
        cst = dt.astimezone(timezone(timedelta(hours=-6)))
        return cst.strftime("%b %d %-I:%M %p CST")
    except (ValueError, AttributeError):
        return close_time[:16] if close_time else ""


def _cleanup_expired():
    """Remove trades older than TRADE_EXPIRY_SECONDS."""
    now = time.time()
    expired = [
        tid for tid, t in _pending_trades.items()
        if now - t["created_at"] > TRADE_EXPIRY_SECONDS
    ]
    for tid in expired:
        del _pending_trades[tid]
        logger.info(f"Expired pending trade {tid}")


async def send_trade_for_approval(
    ticker: str,
    side: str,
    contracts: int,
    price_cents: int,
    strategy: str,
    description: str,
    metadata: dict = None,
) -> Optional[str]:
    """
    Send a trade opportunity to Telegram with APPROVE/SKIP buttons.

    Returns the trade_id if message sent successfully, None otherwise.
    """
    from telegram.bot import get_bot

    _cleanup_expired()

    trade_id = _generate_trade_id()
    cost = (contracts * price_cents) / 100.0
    profit = contracts * (100 - price_cents) / 100.0 if side == "no" else 0
    roi = (profit / cost * 100) if cost > 0 else 0

    _pending_trades[trade_id] = {
        "ticker": ticker,
        "side": side,
        "contracts": contracts,
        "price_cents": price_cents,
        "cost": cost,
        "profit_if_win": profit,
        "strategy": strategy,
        "metadata": metadata or {},
        "created_at": time.time(),
        "status": "pending",
    }

    # Build alert message
    meta = metadata or {}
    win_rate = meta.get("win_rate", 0)
    grade = meta.get("grade", "")
    close_time = meta.get("close_time", "")
    settle_str = _format_settlement_time(close_time)

    lines = [
        f"\U0001f534 <b>TRADE OPPORTUNITY</b> [{strategy.upper()}]",
        "",
        f"<code>{ticker}</code>",
        f"BUY {side.upper()} {contracts}x @ {price_cents}c",
        "",
        f"Risk: <b>${cost:.2f}</b>",
        f"Reward: <b>+${profit:.2f}</b> ({roi:.1f}% ROI)",
    ]
    if win_rate:
        prob_line = f"Win probability: <b>{win_rate:.1%}</b>"
        if grade:
            prob_line += f"  |  Grade: <b>{grade}</b>"
        lines.append(prob_line)
    if settle_str:
        lines.append(f"Settles: <b>{settle_str}</b>")
    lines.append("")
    lines.append(description)
    lines.append("")
    lines.append(f"\u23f0 Expires in 30 min")
    text = "\n".join(lines)

    # Inline keyboard with APPROVE and SKIP buttons
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "\u2705 APPROVE",
                    "callback_data": f"trade_approve:{trade_id}",
                },
                {
                    "text": "\u274c SKIP",
                    "callback_data": f"trade_skip:{trade_id}",
                },
            ]
        ]
    }

    bot = get_bot()
    result = await bot.send_message(text, reply_markup=reply_markup)

    if isinstance(result, dict):
        _pending_trades[trade_id]["message_id"] = result.get("message_id")
        _pending_trades[trade_id]["chat_id"] = str(result.get("chat", {}).get("id", ""))
        logger.info(f"Sent trade approval request {trade_id}: {ticker} {side} {contracts}x @ {price_cents}c")
        return trade_id
    else:
        # Message send failed, clean up
        del _pending_trades[trade_id]
        logger.error(f"Failed to send trade approval for {ticker}")
        return None


async def send_batch_for_approval(
    trades: list[dict],
    strategy: str,
    summary: str,
) -> Optional[str]:
    """
    Send a batch of trades as a single message with APPROVE ALL / SKIP buttons.
    Each trade in the list should have: ticker, side, contracts, price_cents, description, metadata.
    """
    from telegram.bot import get_bot

    _cleanup_expired()

    batch_id = _generate_trade_id()
    total_cost = 0
    total_profit = 0

    lines = [
        f"\U0001f534 <b>TRADE OPPORTUNITIES</b> [{strategy.upper()}]",
        "",
        summary,
        "",
    ]

    trade_entries = []
    for i, t in enumerate(trades):
        cost = (t["contracts"] * t["price_cents"]) / 100.0
        profit = t["contracts"] * (100 - t["price_cents"]) / 100.0 if t["side"] == "no" else 0
        roi = (profit / cost * 100) if cost > 0 else 0
        total_cost += cost
        total_profit += profit

        entry_id = f"{batch_id}_{i}"
        trade_entries.append({
            **t,
            "cost": cost,
            "profit_if_win": profit,
            "entry_id": entry_id,
        })

        lines.append(
            f"<b>{i+1}.</b> <code>{t['ticker']}</code>"
        )
        lines.append(
            f"   {t['side'].upper()} {t['contracts']}x @ {t['price_cents']}c"
            f" = ${cost:.2f} → +${profit:.2f} ({roi:.0f}%)"
        )
        # Show probability, grade, settlement from metadata
        t_meta = t.get("metadata", {})
        detail_parts = []
        t_wr = t_meta.get("win_rate", 0)
        t_grade = t_meta.get("grade", "")
        t_close = t_meta.get("close_time", "")
        if t_wr:
            detail_parts.append(f"{t_wr:.1%} win")
        if t_grade:
            detail_parts.append(f"Grade: {t_grade}")
        t_settle = _format_settlement_time(t_close)
        if t_settle:
            detail_parts.append(f"Settles {t_settle}")
        if detail_parts:
            lines.append(f"   {' | '.join(detail_parts)}")
        if t.get("description"):
            lines.append(f"   {t['description']}")
        lines.append("")

    lines.append(f"<b>Total: ${total_cost:.2f} deployed → +${total_profit:.2f} profit</b>")
    lines.append("")
    lines.append(f"\u23f0 Expires in 30 min")

    _pending_trades[batch_id] = {
        "type": "batch",
        "trades": trade_entries,
        "strategy": strategy,
        "total_cost": total_cost,
        "total_profit": total_profit,
        "created_at": time.time(),
        "status": "pending",
    }

    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": f"\u2705 APPROVE ALL ({len(trades)} trades, ${total_cost:.2f})",
                    "callback_data": f"trade_approve:{batch_id}",
                },
            ],
            [
                {
                    "text": "\u274c SKIP ALL",
                    "callback_data": f"trade_skip:{batch_id}",
                },
            ],
        ]
    }

    bot = get_bot()
    result = await bot.send_message("\n".join(lines), reply_markup=reply_markup)

    if isinstance(result, dict):
        _pending_trades[batch_id]["message_id"] = result.get("message_id")
        _pending_trades[batch_id]["chat_id"] = str(result.get("chat", {}).get("id", ""))
        logger.info(f"Sent batch approval {batch_id}: {len(trades)} trades, ${total_cost:.2f}")
        return batch_id

    del _pending_trades[batch_id]
    return None


async def handle_trade_callback(chat_id: str, message_id: int, callback_data: str, callback_query_id: str):
    """
    Handle APPROVE or SKIP button press.
    Called by the bot's callback query handler.
    """
    from telegram.bot import get_bot
    from pipeline.kalshi_executor import place_order, send_trade_notification

    bot = get_bot()
    _cleanup_expired()

    parts = callback_data.split(":", 1)
    if len(parts) != 2:
        await bot.answer_callback_query(callback_query_id, "Invalid action")
        return

    action = parts[0]  # "trade_approve" or "trade_skip"
    trade_id = parts[1]

    trade = _pending_trades.get(trade_id)
    if not trade:
        await bot.answer_callback_query(callback_query_id, "Trade expired or not found")
        await bot.edit_message_text(chat_id, message_id, "\u23f0 Trade expired or already handled.")
        return

    # Enforce expiry at button press time (not just on cleanup)
    elapsed = time.time() - trade["created_at"]
    if elapsed > TRADE_EXPIRY_SECONDS:
        trade["status"] = "expired"
        del _pending_trades[trade_id]
        await bot.answer_callback_query(callback_query_id, "Trade expired (30+ min old)")
        await bot.edit_message_text(chat_id, message_id, "\u23f0 Trade expired — prices are stale. Wait for next scan.")
        logger.info(f"Trade {trade_id} expired on button press ({elapsed:.0f}s old)")
        return

    if trade["status"] != "pending":
        await bot.answer_callback_query(callback_query_id, f"Already {trade['status']}")
        return

    # ── SKIP ──
    if action == "trade_skip":
        trade["status"] = "skipped"
        await bot.answer_callback_query(callback_query_id, "Skipped")
        await bot.edit_message_text(
            chat_id, message_id,
            f"\u274c <b>SKIPPED</b> — No orders placed.\n\n<i>You chose to skip this opportunity.</i>",
        )
        del _pending_trades[trade_id]
        logger.info(f"Trade {trade_id} skipped by user")
        return

    # ── APPROVE ──
    if action == "trade_approve":
        trade["status"] = "executing"
        await bot.answer_callback_query(callback_query_id, "Executing trades...")

        if trade.get("type") == "batch":
            # Execute all trades in the batch — continue even if one fails
            results = []
            for t in trade["trades"]:
                try:
                    result = place_order(
                        ticker=t["ticker"],
                        side=t["side"],
                        contracts=t["contracts"],
                        price_cents=t["price_cents"],
                        strategy=trade["strategy"],
                        metadata=t.get("metadata", {}),
                    )
                except Exception as e:
                    logger.error(f"Batch trade {t['ticker']} failed: {e}")
                    result = {"status": "error", "error": str(e), "ticker": t["ticker"], "cost": 0}
                results.append(result)
                try:
                    await send_trade_notification(result, trade["strategy"],
                        t.get("description", ""))
                except Exception as e:
                    logger.error(f"Notification failed for {t['ticker']}: {e}")

            filled = sum(1 for r in results if r.get("status") == "filled")
            total = len(results)
            total_cost = sum(r.get("cost", 0) for r in results if r.get("status") == "filled")

            trade["status"] = "executed"
            await bot.edit_message_text(
                chat_id, message_id,
                f"\u2705 <b>APPROVED — {filled}/{total} orders filled</b>\n"
                f"Total deployed: ${total_cost:.2f}\n\n"
                f"<i>Orders placed at {datetime.now().strftime('%I:%M %p CST')}</i>",
            )
            del _pending_trades[trade_id]
            logger.info(f"Batch {trade_id} approved: {filled}/{total} filled, ${total_cost:.2f}")

        else:
            # Single trade
            result = place_order(
                ticker=trade["ticker"],
                side=trade["side"],
                contracts=trade["contracts"],
                price_cents=trade["price_cents"],
                strategy=trade["strategy"],
                metadata=trade.get("metadata", {}),
            )
            await send_trade_notification(result, trade["strategy"])

            trade["status"] = "executed"
            status_emoji = "\u2705" if result.get("status") == "filled" else "\u274c"
            status_text = result.get("status", "unknown").upper()

            await bot.edit_message_text(
                chat_id, message_id,
                f"{status_emoji} <b>{status_text}</b> — {trade['ticker']}\n"
                f"BUY {trade['side'].upper()} {trade['contracts']}x @ {trade['price_cents']}c\n"
                f"Cost: ${trade['cost']:.2f}\n\n"
                f"<i>Executed at {datetime.now().strftime('%I:%M %p CST')}</i>",
            )
            del _pending_trades[trade_id]
            logger.info(f"Trade {trade_id} approved: {result.get('status')}")


def register_trade_callbacks(bot):
    """Register the trade approval callback handler with the bot."""
    bot.register_callback("trade_approve", handle_trade_callback)
    bot.register_callback("trade_skip", handle_trade_callback)
    logger.info("Registered trade approval callbacks")
