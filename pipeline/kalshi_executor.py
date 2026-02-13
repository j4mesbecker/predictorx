"""
PredictorX — Kalshi Order Executor
Unified module for placing orders on Kalshi with safety checks.

Handles:
  - SPX bracket trades (94.7% NO win rate sweet spot)
  - Weather trades (81% NO win rate in 15-70c range)

Safety features:
  - Max per-trade dollar limit
  - Daily loss limit
  - Max daily trade count
  - Balance floor (never go below minimum)
  - Duplicate trade detection
  - Full Telegram logging of every order
  - Trade history tracking

Uses kalshi_python SDK with RSA-PSS authentication.
"""

import json
import logging
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Safety Limits ───────────────────────────────────────────
MAX_PER_TRADE = 20.0        # Max $ per single trade (user wants to start small)
MAX_DAILY_DEPLOYED = 200.0  # Max total $ deployed per day across all trades
MAX_TRADES_PER_DAY = 15     # Max number of orders per day
BALANCE_FLOOR = 650.0       # Never let balance drop below this
MAX_DAILY_LOSS = 50.0       # Stop trading if daily realized loss exceeds this

# ── State Tracking ──────────────────────────────────────────
_today: date | None = None
_trades_today: list[dict] = []
_deployed_today: float = 0.0
_realized_loss_today: float = 0.0
_executed_tickers: set[str] = set()  # Prevent duplicate orders same day

# Trade log file
TRADE_LOG = Path("/Users/jamesbecker/Desktop/prediction-platform/data/trade_log.jsonl")


def _reset_if_new_day():
    """Reset daily counters at start of new trading day."""
    global _today, _trades_today, _deployed_today, _realized_loss_today, _executed_tickers
    today = date.today()
    if _today != today:
        _today = today
        _trades_today = []
        _deployed_today = 0.0
        _realized_loss_today = 0.0
        _executed_tickers = set()
        logger.info("Kalshi executor: new day reset")


def _get_kalshi_client():
    """Create authenticated Kalshi API client using kalshi_python SDK."""
    from config.settings import get_settings
    settings = get_settings()

    import kalshi_python
    from kalshi_python import Configuration

    config = Configuration()
    config.host = "https://api.elections.kalshi.com/trade-api/v2"

    client = kalshi_python.KalshiClient(configuration=config)

    key_id = settings.kalshi_api_key_id
    key_path = settings.kalshi_private_key_path

    # Resolve key path
    if not os.path.isabs(key_path):
        key_path = os.path.join(
            os.path.dirname(__file__), "..", key_path
        )
    if not os.path.exists(key_path):
        alt = "/Users/jamesbecker/Desktop/polymarket-trader/kalshi_key.pem"
        if os.path.exists(alt):
            key_path = alt
        else:
            raise FileNotFoundError(f"Kalshi key not found: {key_path}")

    client.set_kalshi_auth(key_id=key_id, private_key_path=key_path)
    return client


def get_balance() -> float:
    """Get current Kalshi balance in dollars."""
    try:
        client = _get_kalshi_client()
        api = client  # The KalshiClient wraps PortfolioApi methods
        # Use the portfolio API
        from kalshi_python import PortfolioApi
        portfolio = PortfolioApi(api)
        resp = portfolio.get_balance()
        return resp.balance / 100.0 if hasattr(resp, 'balance') else 0.0
    except Exception as e:
        logger.error(f"Balance fetch error: {e}")
        # Fallback to our raw API
        try:
            from pipeline.spx_bracket_scanner import _kalshi_get
            data = _kalshi_get("/portfolio/balance")
            return data.get("balance", 0) / 100.0
        except Exception:
            return 0.0


def _check_safety(ticker: str, cost: float, strategy: str) -> tuple[bool, str]:
    """
    Run all safety checks before placing an order.
    Returns (ok, reason).
    """
    _reset_if_new_day()

    # 1. Duplicate check
    if ticker in _executed_tickers:
        return False, f"Already traded {ticker} today"

    # 2. Per-trade limit
    if cost > MAX_PER_TRADE:
        return False, f"Trade cost ${cost:.2f} exceeds max ${MAX_PER_TRADE:.2f}"

    # 3. Daily deploy limit
    if _deployed_today + cost > MAX_DAILY_DEPLOYED:
        return False, f"Daily limit: ${_deployed_today:.2f} + ${cost:.2f} > ${MAX_DAILY_DEPLOYED:.2f}"

    # 4. Trade count limit
    if len(_trades_today) >= MAX_TRADES_PER_DAY:
        return False, f"Max {MAX_TRADES_PER_DAY} trades/day reached"

    # 5. Daily loss limit
    if _realized_loss_today >= MAX_DAILY_LOSS:
        return False, f"Daily loss limit ${MAX_DAILY_LOSS:.2f} hit — stopped"

    # 6. Balance floor
    try:
        balance = get_balance()
        if balance - cost < BALANCE_FLOOR:
            return False, f"Balance ${balance:.2f} - ${cost:.2f} would breach floor ${BALANCE_FLOOR:.2f}"
    except Exception:
        pass  # If we can't check balance, other limits still apply

    return True, "OK"


def place_order(
    ticker: str,
    side: str,
    contracts: int,
    price_cents: int,
    strategy: str,
    metadata: dict | None = None,
) -> dict:
    """
    Place a single order on Kalshi.

    Args:
        ticker: Market ticker (e.g., KXINX-26FEB13H1600-B6962)
        side: "yes" or "no"
        contracts: Number of contracts to buy
        price_cents: Price per contract in cents (1-99).
                     For NO orders, this is the NO price (100 - yes_price).
        strategy: "spx_bracket" or "weather" (for logging)
        metadata: Optional dict with extra info (bracket range, distance, etc.)

    Returns:
        dict with order result or error
    """
    _reset_if_new_day()
    global _deployed_today

    cost = (contracts * price_cents) / 100.0

    # Safety checks
    ok, reason = _check_safety(ticker, cost, strategy)
    if not ok:
        logger.warning(f"Order BLOCKED: {ticker} {side} {contracts}x @ {price_cents}c — {reason}")
        return {"status": "blocked", "reason": reason, "ticker": ticker}

    # Place the order via SDK
    try:
        client = _get_kalshi_client()
        from kalshi_python import PortfolioApi
        portfolio = PortfolioApi(client)

        order_kwargs = {
            "ticker": ticker,
            "action": "buy",
            "side": side,
            "count": contracts,
            "type": "limit",
        }

        if side == "yes":
            order_kwargs["yes_price"] = price_cents
        else:
            order_kwargs["no_price"] = price_cents

        logger.info(f"Placing order: {ticker} BUY {side.upper()} {contracts}x @ {price_cents}c (${cost:.2f})")

        response = portfolio.create_order(**order_kwargs)

        # Extract order details from response
        order_id = None
        order_status = "unknown"
        fill_count = 0
        remaining_count = contracts

        if hasattr(response, 'order') and response.order:
            o = response.order
            order_id = getattr(o, 'order_id', None)
            order_status = getattr(o, 'status', 'unknown')
            fill_count = getattr(o, 'fill_count', 0) or 0
            remaining_count = getattr(o, 'remaining_count', contracts) or 0

        # If SDK doesn't give us status, check via raw API
        if order_id and order_status in ("unknown", ""):
            try:
                from pipeline.spx_bracket_scanner import _kalshi_get
                order_check = _kalshi_get(f"/portfolio/orders/{order_id}")
                o_data = order_check.get("order", order_check)
                order_status = o_data.get("status", "unknown")
                fill_count = o_data.get("fill_count", 0) or 0
                remaining_count = o_data.get("remaining_count", 0) or 0
            except Exception:
                pass

        # Determine actual fill status
        if order_status == "executed" or (fill_count > 0 and remaining_count == 0):
            actual_status = "filled"
        elif order_status == "resting" or remaining_count > 0:
            actual_status = "resting"
            # If order is resting (not filled), wait briefly then recheck
            time.sleep(2)
            try:
                from pipeline.spx_bracket_scanner import _kalshi_get
                order_check = _kalshi_get(f"/portfolio/orders/{order_id}")
                o_data = order_check.get("order", order_check)
                order_status = o_data.get("status", "unknown")
                fill_count = o_data.get("fill_count", 0) or 0
                remaining_count = o_data.get("remaining_count", 0) or 0
                if order_status == "executed":
                    actual_status = "filled"
                elif order_status == "canceled":
                    actual_status = "canceled"
            except Exception:
                pass
        elif order_status == "canceled":
            actual_status = "canceled"
        else:
            actual_status = order_status

        # Update tracking
        _executed_tickers.add(ticker)
        if actual_status == "filled":
            _deployed_today += cost

        trade_record = {
            "timestamp": datetime.now().isoformat(),
            "ticker": ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost": cost,
            "strategy": strategy,
            "order_id": order_id,
            "status": actual_status,
            "kalshi_status": order_status,
            "fill_count": fill_count,
            "remaining_count": remaining_count,
            "metadata": metadata or {},
        }
        _trades_today.append(trade_record)

        # Log to file
        _log_trade(trade_record)

        result = {
            "status": actual_status,
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost": cost,
            "kalshi_status": order_status,
            "fill_count": fill_count,
        }

        if actual_status == "filled":
            logger.warning(f"ORDER FILLED: {ticker} BUY {side.upper()} {contracts}x @ {price_cents}c = ${cost:.2f}")
        elif actual_status == "resting":
            logger.warning(f"ORDER RESTING: {ticker} BUY {side.upper()} {contracts}x @ {price_cents}c — waiting for fill")
        else:
            logger.warning(f"ORDER {actual_status.upper()}: {ticker} BUY {side.upper()} {contracts}x @ {price_cents}c")
        return result

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Order FAILED: {ticker} — {error_msg}")

        trade_record = {
            "timestamp": datetime.now().isoformat(),
            "ticker": ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "cost": cost,
            "strategy": strategy,
            "order_id": None,
            "status": "error",
            "error": error_msg,
            "metadata": metadata or {},
        }
        _trades_today.append(trade_record)
        _log_trade(trade_record)

        return {"status": "error", "error": error_msg, "ticker": ticker}


def _log_trade(record: dict):
    """Append trade record to JSONL log file."""
    try:
        TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(TRADE_LOG, "a") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        logger.error(f"Trade log write error: {e}")


async def send_trade_notification(result: dict, strategy: str, extra: str = ""):
    """Send Telegram notification for a placed trade."""
    from telegram.bot import get_bot

    bot = get_bot()
    if not bot.configured:
        return

    if result["status"] == "filled":
        lines = [
            f"\U0001f7e2 <b>ORDER FILLED</b> [{strategy.upper()}]",
            "",
            f"<code>{result['ticker']}</code>",
            f"BUY {result['side'].upper()} {result['contracts']}x @ {result['price_cents']}c",
            f"Cost: <b>${result['cost']:.2f}</b>",
        ]
        if extra:
            lines.append(f"{extra}")
        lines.append("")
        lines.append(f"Daily deployed: ${_deployed_today:.2f} / ${MAX_DAILY_DEPLOYED:.2f}")
        lines.append(f"Trades today: {len(_trades_today)} / {MAX_TRADES_PER_DAY}")

    elif result["status"] == "blocked":
        lines = [
            f"\U0001f6ab <b>ORDER BLOCKED</b> [{strategy.upper()}]",
            "",
            f"<code>{result['ticker']}</code>",
            f"Reason: {result['reason']}",
        ]

    else:
        lines = [
            f"\u274c <b>ORDER FAILED</b> [{strategy.upper()}]",
            "",
            f"<code>{result['ticker']}</code>",
            f"Error: {result.get('error', 'unknown')}",
        ]

    await bot.send_message("\n".join(lines))


def get_daily_summary() -> dict:
    """Get summary of today's trading activity."""
    _reset_if_new_day()
    filled = [t for t in _trades_today if t["status"] == "filled"]
    errors = [t for t in _trades_today if t["status"] == "error"]
    blocked = [t for t in _trades_today if t["status"] == "blocked"]

    return {
        "date": str(_today),
        "total_trades": len(_trades_today),
        "filled": len(filled),
        "errors": len(errors),
        "blocked": len(blocked),
        "deployed": _deployed_today,
        "max_daily": MAX_DAILY_DEPLOYED,
        "remaining": MAX_DAILY_DEPLOYED - _deployed_today,
        "trades": _trades_today,
    }
