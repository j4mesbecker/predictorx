"""
PredictorX — SPX Bracket Scanner
Scans live Kalshi SPX (INX) bracket markets and fires Telegram alerts
with specific trade recommendations.

Designed for catalyst days (CPI, FOMC, NFP):
  1. Waits until after catalyst data drops (e.g., CPI at 7:30 AM CST)
  2. Fetches current SPX price
  3. Scans Kalshi brackets 75-150 points away from current SPX
  4. Finds brackets in the 10-49c YES sweet spot (94.7% NO win rate)
  5. Sends Telegram alert with exact trade recommendations

Also runs as a daily scanner during market hours for non-catalyst days.

Backed by 10,000 settled market analysis:
  - 25-point brackets, SPX lands in-bracket only 5.9% of time
  - NO sweet spot (YES 10-49c): 94.7% WR, +17.4% ROI, 531 trades
"""

import hashlib
import json
import logging
import time
import urllib.request
from base64 import b64encode
from datetime import date, datetime, timedelta
from pathlib import Path

from config.constants import BLACKOUT_DATES

logger = logging.getLogger(__name__)

# ── Kalshi API Configuration ────────────────────────────────
API_BASE = "https://api.elections.kalshi.com"
API_PREFIX = "/trade-api/v2"

# ── Bracket Risk Limits ─────────────────────────────────────
BRACKET_MAX_PER_TRADE = 10.0   # $10 max risk per bracket trade
BRACKET_DAILY_BUDGET = 20.0    # $20 max total bracket risk per day

# ── State Tracking ──────────────────────────────────────────
_last_scan_date: date | None = None
_scans_today: int = 0
_alerted_tickers: set[str] = set()  # Don't alert same bracket twice per day


def _reset_if_new_day():
    global _last_scan_date, _scans_today, _alerted_tickers
    today = date.today()
    if _last_scan_date != today:
        _last_scan_date = today
        _scans_today = 0
        _alerted_tickers = set()
        logger.info("SPX bracket scanner: new day reset")


def _get_kalshi_credentials():
    """Load Kalshi API credentials from settings."""
    from config.settings import get_settings
    settings = get_settings()
    key_id = settings.kalshi_api_key_id
    key_path = Path(settings.kalshi_private_key_path)
    if not key_path.exists():
        # Try common locations
        alt_path = Path("/Users/jamesbecker/Desktop/polymarket-trader/kalshi_key.pem")
        if alt_path.exists():
            key_path = alt_path
        else:
            raise FileNotFoundError(f"Kalshi private key not found at {key_path}")
    return key_id, key_path


def _sign_request(private_key, method: str, path: str, timestamp_ms: int) -> str:
    """RSA-PSS sign a Kalshi API request."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, utils

    message = f"{timestamp_ms}{method}{path}".encode()
    msg_hash = hashlib.sha256(message).digest()
    signature = private_key.sign(
        msg_hash,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        utils.Prehashed(hashes.SHA256()),
    )
    return b64encode(signature).decode()


def _load_private_key(key_path: Path):
    """Load RSA private key from PEM file."""
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    with open(key_path, "rb") as f:
        return load_pem_private_key(f.read(), password=None)


def _kalshi_get(path: str, params: dict = None) -> dict:
    """Make an authenticated GET request to Kalshi API."""
    key_id, key_path = _get_kalshi_credentials()
    private_key = _load_private_key(key_path)

    full_path = f"{API_PREFIX}{path}"
    sign_path = full_path.split("?")[0]  # Strip query params for signing

    timestamp_ms = int(time.time() * 1000)
    signature = _sign_request(private_key, "GET", sign_path, timestamp_ms)

    url = f"{API_BASE}{full_path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"

    req = urllib.request.Request(url)
    req.add_header("KALSHI-ACCESS-KEY", key_id)
    req.add_header("KALSHI-ACCESS-SIGNATURE", signature)
    req.add_header("KALSHI-ACCESS-TIMESTAMP", str(timestamp_ms))
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")

    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _fetch_spx_price() -> dict | None:
    """Fetch current SPX price from Yahoo Finance."""
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1m&range=1d"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "PredictorX/1.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        meta = data["chart"]["result"][0]["meta"]
        return {
            "price": meta.get("regularMarketPrice", 0),
            "prev_close": meta.get("previousClose", 0),
            "open": meta.get("regularMarketOpen", 0),
        }
    except Exception as e:
        logger.debug(f"SPX price fetch failed: {e}")
        return None


def _fetch_spx_brackets() -> list[dict]:
    """
    Fetch all open Kalshi SPX/INX bracket markets.
    Uses series_ticker=KXINX to get all S&P 500 bracket markets directly.
    Returns list of dicts with: ticker, title, yes_bid, yes_ask, bracket_low, bracket_high, etc.
    """
    import re
    all_markets = []
    cursor = None

    for _ in range(10):  # Max 10 pages
        params = {"series_ticker": "KXINX", "limit": "200", "status": "open"}
        if cursor:
            params["cursor"] = cursor

        try:
            data = _kalshi_get("/markets", params)
        except Exception as e:
            logger.error(f"Kalshi KXINX market fetch failed: {e}")
            break

        markets = data.get("markets", [])
        next_cursor = data.get("cursor")

        for m in markets:
            ticker = m.get("ticker", "")

            # Only bracket markets (B prefix), skip thresholds (T prefix)
            if "-B" not in ticker:
                continue

            # Parse bracket range from title
            # Format: "Will the S&P 500 be between 6,925 and 6,949.9999 on Feb 13, 2026 at 4pm EST?"
            title = m.get("title", "")
            range_match = re.search(
                r"between\s+([\d,]+(?:\.\d+)?)\s+and\s+([\d,]+(?:\.\d+)?)",
                title, re.IGNORECASE
            )
            if not range_match:
                continue

            bracket_low = float(range_match.group(1).replace(",", ""))
            bracket_high = float(range_match.group(2).replace(",", ""))
            bracket_mid = (bracket_low + bracket_high) / 2

            # Determine event type from ticker
            # H1600 = 4pm EST settlement (standard daily close)
            event_type = "daily"
            if re.search(r"H\d{4}", ticker):
                event_type = "hourly"  # These are technically "at close" but Kalshi calls them hourly

            yes_bid = m.get("yes_bid", 0) or 0
            yes_ask = m.get("yes_ask", 0) or 0
            no_bid = m.get("no_bid", 0) or 0
            no_ask = m.get("no_ask", 0) or 0
            volume = m.get("volume", 0) or 0

            all_markets.append({
                "ticker": ticker,
                "title": title,
                "event_ticker": m.get("event_ticker", ""),
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "no_bid": no_bid,
                "no_ask": no_ask,
                "volume": volume,
                "close_time": m.get("close_time", ""),
                "bracket_low": bracket_low,
                "bracket_high": bracket_high,
                "bracket_mid": bracket_mid,
                "event_type": event_type,
            })

        if not next_cursor or not markets:
            break
        cursor = next_cursor

    logger.info(f"Fetched {len(all_markets)} SPX bracket markets from Kalshi")
    return all_markets


def _filter_sweet_spot(markets: list[dict], spx_price: float) -> list[dict]:
    """
    Filter markets to the sweet spot:
    - YES priced 10-49c
    - 50-200 points away from current SPX price
    - Settling within 48 hours (favor near-term)
    """
    from core.strategies.spx_edge_map import get_spx_edge_signal

    sweet = []
    for m in markets:
        yes_price = m["yes_ask"] if m["yes_ask"] > 0 else m["yes_bid"]
        if yes_price <= 0:
            continue
        if not (10 <= yes_price <= 49):
            continue

        # Distance from current SPX
        distance = abs(spx_price - m["bracket_mid"])
        if distance < 50:
            continue  # Too close — SPX could easily reach this bracket

        # Get edge signal
        signal = get_spx_edge_signal(
            market_price_cents=yes_price,
            event_type=m.get("event_type", "daily"),
            distance_from_spx=distance,
        )

        if signal["grade"] in ("F",) or signal["edge"] <= 0:
            continue

        m["signal"] = signal
        m["distance"] = distance
        m["yes_price"] = yes_price
        sweet.append(m)

    # Sort by grade (A+ first), then by edge
    grade_order = {"A+": 0, "A": 1, "B": 2, "C": 3}
    sweet.sort(key=lambda x: (
        grade_order.get(x["signal"]["grade"], 4),
        -x["signal"]["edge"],
    ))

    return sweet


async def scan_spx_brackets(force: bool = False):
    """
    Main scanning function. Called by scheduler or manually.
    Scans live Kalshi SPX brackets and sends Telegram alerts.

    Args:
        force: If True, scan even if already scanned recently
    """
    # Skip market holidays (Presidents Day, etc.) — no SPX movement
    from pipeline.tasks import is_market_open_today
    if not is_market_open_today() and not force:
        logger.info("SPX bracket scanner: market holiday — skipping")
        return

    global _scans_today

    _reset_if_new_day()

    today = date.today()
    today_str = today.strftime("%Y-%m-%d")
    is_catalyst_day = today_str in BLACKOUT_DATES

    # Limit scans per day — generous since we require user approval now
    # Morning window (8-10 AM ET) is most important, but keep scanning all day
    max_scans = 20 if is_catalyst_day else 12
    if _scans_today >= max_scans and not force:
        return

    _scans_today += 1

    # ── Fetch current SPX price ────────────────────────────
    spx_data = _fetch_spx_price()
    if not spx_data or not spx_data["price"]:
        logger.debug("SPX bracket scanner: no price data")
        return

    spx_price = spx_data["price"]
    prev_close = spx_data["prev_close"]
    change_pct = ((spx_price - prev_close) / prev_close * 100) if prev_close else 0

    # ── Fetch VIX for regime context ───────────────────────
    vix_price = 0
    regime = "MEDIUM"
    try:
        from adapters.kalshi_data import get_vix
        vix_data = get_vix()
        vix_price = vix_data.get("price", 0)
        regime = vix_data.get("regime", "MEDIUM")
    except Exception:
        pass

    # ── Fetch Kalshi SPX brackets ──────────────────────────
    try:
        markets = _fetch_spx_brackets()
    except Exception as e:
        logger.error(f"SPX bracket fetch failed: {e}")
        return

    if not markets:
        logger.info("No SPX bracket markets found on Kalshi")
        return

    # ── Filter to sweet spot ───────────────────────────────
    sweet_spot = _filter_sweet_spot(markets, spx_price)

    if not sweet_spot:
        logger.info(f"No sweet spot brackets found (SPX {spx_price:,.0f}, {len(markets)} total)")
        return

    # ── Filter out already-alerted tickers ──────────────────
    new_opportunities = [m for m in sweet_spot if m["ticker"] not in _alerted_tickers]

    if not new_opportunities:
        logger.debug("All sweet spot brackets already alerted today")
        return

    # Take top 5 opportunities
    top = new_opportunities[:5]

    # Mark as alerted
    for m in top:
        _alerted_tickers.add(m["ticker"])

    # ── Get balance for sizing ──────────────────────────────
    balance = None
    try:
        data = _kalshi_get("/portfolio/balance")
        balance = data.get("balance", 0) / 100.0  # Kalshi returns cents
    except Exception as e:
        logger.error(f"Balance fetch failed: {e}")

    if not balance or balance <= 0:
        logger.error("Cannot fetch balance — aborting bracket scan (no stale fallback)")
        return

    # ── Build trade recommendations ──────────────────────────
    from core.strategies.spx_edge_map import get_spx_trade_recommendation

    trades = []
    for m in top:
        rec = get_spx_trade_recommendation(
            market_price_cents=m["yes_price"],
            balance=balance,
            event_type=m.get("event_type", "daily"),
            distance_from_spx=m["distance"],
            max_per_trade=BRACKET_MAX_PER_TRADE,
        )
        rec["ticker"] = m["ticker"]
        rec["title"] = m["title"]
        rec["bracket_low"] = m["bracket_low"]
        rec["bracket_high"] = m["bracket_high"]
        rec["yes_price"] = m["yes_price"]
        rec["distance"] = m["distance"]
        trades.append(rec)

    # ── SEND FOR APPROVAL: Alert user on Telegram with APPROVE/SKIP buttons ──
    from telegram.trade_approvals import send_batch_for_approval

    # Fetch live orderbook prices for each ticker so we show real costs
    approval_trades = []
    batch_cost_so_far = 0.0  # Track cost within this batch (not yet deployed)
    for t in trades:
        if t.get("action") == "SKIP":
            continue

        side = t.get("side", "no")
        contracts = t.get("contracts", 0)

        if contracts <= 0:
            continue

        ticker = t["ticker"]

        # Get live price from orderbook
        try:
            mkt = _kalshi_get(f"/markets/{ticker}")["market"]
            no_ask = mkt.get("no_ask", 0) or 0
            no_bid = mkt.get("no_bid", 0) or 0
            yes_ask = mkt.get("yes_ask", 0) or 0
            yes_bid = mkt.get("yes_bid", 0) or 0
        except Exception as e:
            logger.warning(f"Could not fetch live price for {ticker}: {e}")
            continue

        if side == "no":
            price_cents = no_ask
            if price_cents <= 0:
                price_cents = 100 - (yes_bid or t["yes_price"])
        else:
            price_cents = yes_ask
            if price_cents <= 0:
                price_cents = t["yes_price"]

        # Skip if NO price is too high (no profit margin)
        if side == "no" and price_cents > 92:
            logger.info(f"Skipping {ticker}: NO ask {price_cents}c too high")
            continue

        # Recalculate contracts based on live price
        if price_cents > 0:
            max_cost = min(BRACKET_MAX_PER_TRADE, t.get("cost", BRACKET_MAX_PER_TRADE))
            contracts = int((max_cost * 100) / price_cents)
            if contracts <= 0:
                continue

        # Enforce daily bracket budget using executor's actual deployed amount
        trade_cost = (contracts * price_cents) / 100.0
        from pipeline.kalshi_executor import get_deployed_today
        deployed = get_deployed_today()
        remaining_budget = BRACKET_DAILY_BUDGET - deployed - batch_cost_so_far
        if remaining_budget <= 0:
            logger.info(f"Daily bracket budget ${BRACKET_DAILY_BUDGET:.0f} exhausted (deployed ${deployed:.2f}), skipping {ticker}")
            break
        if trade_cost > remaining_budget:
            # Reduce contracts to fit remaining budget
            contracts = int((remaining_budget * 100) / price_cents)
            if contracts <= 0:
                logger.info(f"Not enough budget for {ticker}, skipping")
                continue
            trade_cost = (contracts * price_cents) / 100.0

        batch_cost_so_far += trade_cost

        approval_trades.append({
            "ticker": ticker,
            "side": side,
            "contracts": contracts,
            "price_cents": price_cents,
            "description": (
                f"SPX {t['bracket_low']:,.0f}-{t['bracket_high']:,.0f}"
                f" | {t['distance']:.0f}pts away"
                f" | {t.get('win_rate', 0):.1%} WR"
                f" | Grade: {t.get('grade', '?')}"
            ),
            "metadata": {
                "bracket_low": t["bracket_low"],
                "bracket_high": t["bracket_high"],
                "distance": t["distance"],
                "win_rate": t.get("win_rate", 0),
                "edge": t.get("edge", 0),
                "grade": t.get("grade", ""),
                "spx_price": spx_price,
                "vix": vix_price,
                "live_no_ask": no_ask,
                "live_yes_ask": yes_ask,
                "close_time": mkt.get("close_time", ""),
            },
        })

    filled_count = 0
    if approval_trades:
        summary = (
            f"SPX @ {spx_price:,.0f} ({change_pct:+.1f}%)"
            f" | VIX {vix_price:.1f} ({regime})"
            f" | {len(approval_trades)} brackets in sweet spot"
        )
        await send_batch_for_approval(approval_trades, "spx_bracket", summary)
        logger.warning(
            f"SPX BRACKET: Sent {len(approval_trades)} trades for approval "
            f"(SPX {spx_price:,.0f})"
        )

    # Log scan results (the approval message IS the alert now)
    if not approval_trades:
        logger.info(
            f"SPX BRACKET SCAN: no viable trades "
            f"from {len(sweet_spot)} sweet spot "
            f"(SPX {spx_price:,.0f}, {change_pct:+.1f}%)"
        )


async def _send_bracket_alert(alert: dict):
    """Format and send SPX bracket alert via Telegram (legacy, kept for compatibility)."""
    from telegram.bot import get_bot
    from telegram.formatters import format_spx_bracket_alert

    bot = get_bot()
    if not bot.configured:
        return

    text = format_spx_bracket_alert(alert)
    await bot.send_message(text)
