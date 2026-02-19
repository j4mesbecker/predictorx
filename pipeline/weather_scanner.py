"""
PredictorX — Weather Market Scanner
Fetches LIVE Kalshi weather markets and finds NO opportunities.

Two-tier weather NO strategy backed by 16,347 settled markets:
  1. FAR-OUT NO: YES 1-14c → NO wins 99-100%, n=11,842
  2. SWEET SPOT NO: YES 15-70c → 81% NO WR, +15.2% ROI
  - Best cities: LAX (+53%), DEN (+34%), PHI (+28%), CHI (+21%)
  - Best months: Dec (+34%), Jan (+31%), Mar (+28%)
  - Worst: Oct (-24%), Sep (-11%), Feb (-5%)
"""

import logging
import re
from datetime import date, datetime, timedelta

from pipeline.spx_bracket_scanner import _kalshi_get
from core.strategies.weather_edge_map import get_edge_signal

logger = logging.getLogger(__name__)

# ── Weather Trade Limits ───────────────────────────────────
WEATHER_MAX_PER_TRADE = 15.0      # $15 max per sweet spot weather trade
WEATHER_DAILY_BUDGET = 100.0      # $100 max sweet spot weather per day
WEATHER_FAROUT_MAX = 20.0         # $20 max per far-out weather NO trade
WEATHER_FAROUT_BUDGET = 50.0      # $50 max far-out weather NO per day

# ── Kalshi Weather Series ──────────────────────────────────
# Maps internal city code to Kalshi series ticker prefix
WEATHER_SERIES = {
    "CHI": "KXHIGHCHI",
    "NY":  "KXHIGHNY",
    "PHI": "KXHIGHPHIL",
    "MIA": "KXHIGHMIA",
    "AUS": "KXHIGHAUS",
    "DEN": "KXHIGHDEN",
    "LAX": "KXHIGHLAX",
}

# Map Kalshi series back to internal city code for edge map lookup
SERIES_TO_CITY = {v: k for k, v in WEATHER_SERIES.items()}

# ── State Tracking ─────────────────────────────────────────
_last_scan_date: date | None = None
_scans_today: int = 0
_alerted_tickers: set[str] = set()


def _reset_if_new_day():
    global _last_scan_date, _scans_today, _alerted_tickers
    today = date.today()
    if _last_scan_date != today:
        _last_scan_date = today
        _scans_today = 0
        _alerted_tickers = set()
        logger.info("Weather scanner: new day reset")


def _parse_market_type(ticker: str, subtitle: str) -> str:
    """Determine market type from ticker format.

    -T{n} = threshold market (e.g., '44° or below', '53° or above')
    -B{n} = bracket market (e.g., '47° to 48°')
    """
    if "-B" in ticker:
        return "HIGH_BRACKET"
    elif "-T" in ticker:
        if "below" in subtitle.lower() or "under" in subtitle.lower():
            return "LOW_THRESHOLD"
        return "HIGH_THRESHOLD"
    return "HIGH_THRESHOLD"


def _parse_threshold(ticker: str) -> float | None:
    """Extract the temperature threshold/midpoint from a ticker.

    KXHIGHCHI-26FEB20-T45 → 45.0
    KXHIGHCHI-26FEB20-B47.5 → 47.5
    """
    match = re.search(r"-[TB](\d+\.?\d*)", ticker)
    if match:
        return float(match.group(1))
    return None


def _fetch_weather_markets() -> list[dict]:
    """Fetch all open weather markets from Kalshi across all cities."""
    all_markets = []

    for city_code, series in WEATHER_SERIES.items():
        try:
            cursor = None
            for _ in range(5):  # Max 5 pages per city
                params = {"series_ticker": series, "status": "open", "limit": "200"}
                if cursor:
                    params["cursor"] = cursor

                data = _kalshi_get("/markets", params)
                markets = data.get("markets", [])

                for m in markets:
                    ticker = m.get("ticker", "")
                    yes_bid = m.get("yes_bid", 0) or 0
                    yes_ask = m.get("yes_ask", 0) or 0
                    no_bid = m.get("no_bid", 0) or 0
                    no_ask = m.get("no_ask", 0) or 0
                    volume = m.get("volume", 0) or 0
                    subtitle = m.get("subtitle", "")

                    # Parse close time
                    close_time = m.get("close_time", "")

                    all_markets.append({
                        "ticker": ticker,
                        "title": m.get("title", ""),
                        "subtitle": subtitle,
                        "event_ticker": m.get("event_ticker", ""),
                        "city_code": city_code,
                        "series": series,
                        "yes_bid": yes_bid,
                        "yes_ask": yes_ask,
                        "no_bid": no_bid,
                        "no_ask": no_ask,
                        "volume": volume,
                        "close_time": close_time,
                        "market_type": _parse_market_type(ticker, subtitle),
                        "threshold": _parse_threshold(ticker),
                    })

                next_cursor = data.get("cursor")
                if not next_cursor or not markets:
                    break
                cursor = next_cursor

        except Exception as e:
            logger.error(f"Weather market fetch failed for {series}: {e}")

    logger.info(f"Fetched {len(all_markets)} weather markets from Kalshi across {len(WEATHER_SERIES)} cities")
    return all_markets


def _filter_sweet_spot(markets: list[dict]) -> list[dict]:
    """Filter weather markets to two NO zones:
    1. FAR-OUT NO: YES 1-14c → NO wins 99-100%
    2. SWEET SPOT NO: YES 15-70c → 81% WR
    """
    month = datetime.now().month
    sweet = []

    for m in markets:
        yes_price = m["yes_ask"] if m["yes_ask"] > 0 else m["yes_bid"]
        if yes_price <= 0:
            continue

        # Accept two zones: far-out (1-14c) and sweet spot (15-70c)
        if not (1 <= yes_price <= 70):
            continue

        # Skip very low volume for sweet spot (far-out can be low volume)
        if yes_price >= 15 and m["volume"] < 5:
            continue

        # Map city code for edge map lookup
        edge_city = m["city_code"]
        city_remap = {"NY": "NYC", "PHI": "PHI"}
        edge_city = city_remap.get(edge_city, edge_city)

        signal = get_edge_signal(
            city=edge_city,
            market_price_cents=yes_price,
            month=month,
            market_type=m["market_type"],
        )

        if signal["grade"] in ("F",) or signal["edge"] <= 0:
            continue

        m["signal"] = signal
        m["yes_price"] = yes_price
        m["zone"] = "farout" if yes_price < 15 else "sweet_spot"
        sweet.append(m)

    # Sort: far-out NO first (safest), then by grade, then by edge
    grade_order = {"A+": 0, "A": 1, "B": 2, "C": 3}
    sweet.sort(key=lambda x: (
        0 if x["zone"] == "farout" else 1,
        grade_order.get(x["signal"]["grade"], 4),
        -x["signal"]["edge"],
    ))

    return sweet


async def scan_weather_markets(force: bool = False):
    """
    Main weather scanning function. Called by scheduler or manually.
    Fetches live Kalshi weather markets and sends trade alerts via Telegram.
    """
    global _scans_today

    _reset_if_new_day()

    max_scans = 10
    if _scans_today >= max_scans and not force:
        return

    _scans_today += 1

    # ── Fetch all weather markets from Kalshi ─────────────
    try:
        markets = _fetch_weather_markets()
    except Exception as e:
        logger.error(f"Weather market fetch failed: {e}")
        return

    if not markets:
        logger.info("No weather markets found on Kalshi")
        return

    # ── Filter to sweet spot ──────────────────────────────
    sweet_spot = _filter_sweet_spot(markets)

    if not sweet_spot:
        logger.info(f"No weather sweet spot markets found ({len(markets)} total scanned)")
        return

    # ── Filter out already-alerted tickers ─────────────────
    new_opportunities = [m for m in sweet_spot if m["ticker"] not in _alerted_tickers]

    if not new_opportunities:
        logger.debug("All weather sweet spot markets already alerted today")
        return

    # Take top 5 opportunities
    top = new_opportunities[:5]

    for m in top:
        _alerted_tickers.add(m["ticker"])

    # ── Get balance for sizing ─────────────────────────────
    balance = None
    try:
        data = _kalshi_get("/portfolio/balance")
        balance = data.get("balance", 0) / 100.0
    except Exception as e:
        logger.error(f"Balance fetch failed: {e}")

    if not balance or balance <= 0:
        logger.error("Cannot fetch balance — aborting weather scan")
        return

    # ── Build trade recommendations ────────────────────────
    from telegram.trade_approvals import send_batch_for_approval
    from pipeline.kalshi_executor import get_deployed_today

    approval_trades = []
    farout_cost_so_far = 0.0
    sweet_cost_so_far = 0.0

    for m in top:
        side = m["signal"]["side"]
        signal = m["signal"]
        zone = m.get("zone", "sweet_spot")

        # Only take NO trades
        if side != "no":
            continue

        # Get live price
        no_ask = m["no_ask"]
        if no_ask <= 0:
            no_ask = 100 - (m["yes_bid"] or m["yes_price"])
        if no_ask <= 0:
            continue

        price_cents = no_ask

        # Skip if NO price too high (no profit)
        if price_cents > 99:
            continue

        # Size based on zone
        max_cost = WEATHER_FAROUT_MAX if zone == "farout" else WEATHER_MAX_PER_TRADE
        contracts = int((max_cost * 100) / price_cents)
        if contracts <= 0:
            continue

        trade_cost = (contracts * price_cents) / 100.0

        # Zone-specific budget check
        deployed = get_deployed_today()
        if zone == "farout":
            remaining_budget = WEATHER_FAROUT_BUDGET - deployed - farout_cost_so_far
        else:
            remaining_budget = WEATHER_DAILY_BUDGET - deployed - sweet_cost_so_far

        if remaining_budget <= 0:
            budget_name = "far-out" if zone == "farout" else "sweet spot"
            logger.info(f"Weather {budget_name} budget exhausted (deployed ${deployed:.2f})")
            continue
        if trade_cost > remaining_budget:
            contracts = int((remaining_budget * 100) / price_cents)
            if contracts <= 0:
                continue
            trade_cost = (contracts * price_cents) / 100.0

        if zone == "farout":
            farout_cost_so_far += trade_cost
        else:
            sweet_cost_so_far += trade_cost

        zone_label = "FAR-OUT" if zone == "farout" else "SWEET"
        approval_trades.append({
            "ticker": m["ticker"],
            "side": "no",
            "contracts": contracts,
            "price_cents": price_cents,
            "description": (
                f"{m['city_code']} | {zone_label} NO"
                f" | {m['subtitle']}"
                f" | {signal['edge']:.0%} edge"
                f" | {signal['win_rate']:.0%} WR"
                f" | Grade: {signal['grade']}"
            ),
            "metadata": {
                "city": m["city_code"],
                "zone": zone,
                "edge": signal["edge"],
                "win_rate": signal["win_rate"],
                "grade": signal["grade"],
                "threshold": m.get("threshold"),
                "market_type": m["market_type"],
                "yes_price": m["yes_price"],
                "volume": m["volume"],
                "close_time": m["close_time"],
            },
        })

    if approval_trades:
        summary = (
            f"Weather NO sweet spot | {len(sweet_spot)} markets in range"
            f" | {len(approval_trades)} trades"
        )
        await send_batch_for_approval(approval_trades, "weather", summary)
        logger.warning(
            f"WEATHER: Sent {len(approval_trades)} trades for approval "
            f"(from {len(sweet_spot)} sweet spot markets)"
        )
    else:
        logger.info(
            f"WEATHER SCAN: no viable trades from {len(sweet_spot)} sweet spot "
            f"({len(markets)} total scanned)"
        )
