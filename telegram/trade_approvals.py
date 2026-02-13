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
from datetime import datetime
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
    lines = [
        f"\U0001f534 <b>TRADE OPPORTUNITY</b> [{strategy.upper()}]",
        "",
        f"<code>{ticker}</code>",
        f"BUY {side.upper()} {contracts}x @ {price_cents}c",
        f"Cost: <b>${cost:.2f}</b>",
        f"Payout if right: <b>${contracts:.2f}</b> (+${profit:.2f})",
        f"ROI: <b>{roi:.1f}%</b>",
        "",
        description,
        "",
        f"\u23f0 Expires in 30 min",
    ]
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
            # Execute all trades in the batch
            results = []
            for t in trade["trades"]:
                result = place_order(
                    ticker=t["ticker"],
                    side=t["side"],
                    contracts=t["contracts"],
                    price_cents=t["price_cents"],
                    strategy=trade["strategy"],
                    metadata=t.get("metadata", {}),
                )
                results.append(result)
                await send_trade_notification(result, trade["strategy"],
                    t.get("description", ""))

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
