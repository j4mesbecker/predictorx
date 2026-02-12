"""
PredictorX — Stock Level Monitor
Polls individual stocks every 2 min during market hours (9:30 AM - 4 PM ET).
Fires Telegram alerts when key technical levels are hit or approached.

Currently tracking:
  TSLA — Breakout play ($363 support → $441 breakout → $500/$572/$700 targets)
  NVDA — Supply/demand zones ($171 demand → $194 supply → $212 ATH)

Alerts fire once per level per day. Proximity alerts (within 2%) fire separately.
Each level-hit alert includes a Finviz chart image.
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)

# ── TSLA Key Levels (TrendSpider / EliteOptionsTrader) ──────
TSLA_LEVELS = {
    "TSLA_SUPPORT_363": {
        "price": 363.0,
        "label": "Support Floor",
        "direction": "below",
        "action": "DANGER — September breakout level. If lost, next support $290.",
        "trade": "Close all TSLA longs. Do NOT buy calls.",
    },
    "TSLA_BREAKOUT_441": {
        "price": 441.0,
        "label": "Breakout Trigger",
        "direction": "above",
        "action": "BREAKOUT — 4-month consolidation resolved upward.",
        "trade": "BUY TSLA 3/20 500C. Target $500 on momentum.",
    },
    "TSLA_TARGET_500": {
        "price": 500.0,
        "label": "$3T Valuation",
        "direction": "above",
        "action": "$500 HIT — $3 Trillion valuation zone.",
        "trade": "Sell half of 500C position. Let rest ride to $572.",
    },
    "TSLA_RESISTANCE_572": {
        "price": 572.0,
        "label": "Prior High",
        "direction": "above",
        "action": "PRIOR HIGH — All-time resistance zone.",
        "trade": "Tighten stops. Sell remaining 500C. Consider 600C.",
    },
    "TSLA_TARGET_700": {
        "price": 700.0,
        "label": "Year-End Target",
        "direction": "above",
        "action": "$700 TARGET — EliteOptionsTrader year-end PT reached.",
        "trade": "Full exit on remaining TSLA calls. Reassess.",
    },
}

# ── NVDA Supply/Demand Zones ────────────────────────────────
# Based on recent price action, volume profile, and key pivots
NVDA_LEVELS = {
    "NVDA_DEMAND_130": {
        "price": 130.0,
        "label": "Deep Demand Zone",
        "direction": "below",
        "action": "DEEP DEMAND — Major institutional accumulation zone.",
        "trade": "Aggressive BUY. Load NVDA calls 60+ DTE. This is the gift.",
    },
    "NVDA_DEMAND_150": {
        "price": 150.0,
        "label": "Demand Zone",
        "direction": "below",
        "action": "DEMAND ZONE — Strong buyer support from Oct-Nov base.",
        "trade": "BUY NVDA calls 45+ DTE. High conviction long entry.",
    },
    "NVDA_DEMAND_171": {
        "price": 171.0,
        "label": "30d Low Support",
        "direction": "below",
        "action": "30-DAY LOW — Recent demand floor being tested.",
        "trade": "BUY dip if VIX < 25. NVDA 200C 45+ DTE.",
    },
    "NVDA_SUPPLY_194": {
        "price": 194.0,
        "label": "30d High / Supply",
        "direction": "above",
        "action": "SUPPLY ZONE — 30-day high. Sellers expected here.",
        "trade": "Take profits on swing calls. Watch for rejection or breakout.",
    },
    "NVDA_BREAKOUT_200": {
        "price": 200.0,
        "label": "$200 Psychological",
        "direction": "above",
        "action": "$200 BREAKOUT — Psychological resistance cleared.",
        "trade": "Momentum BUY. NVDA 220C 30+ DTE. Next stop ATH.",
    },
    "NVDA_ATH_212": {
        "price": 212.0,
        "label": "All-Time High",
        "direction": "above",
        "action": "ATH BREAKOUT — Blue sky above. No overhead resistance.",
        "trade": "Trail stops. Let winners run. Consider 250C lottos.",
    },
}

# All tracked stocks
WATCHED_STOCKS = {
    "TSLA": {"levels": TSLA_LEVELS, "chart_ticker": "TSLA"},
    "NVDA": {"levels": NVDA_LEVELS, "chart_ticker": "NVDA"},
}

# Proximity alert threshold
PROXIMITY_PCT = 2.0

# ── Daily State ──────────────────────────────────────────────
_fired_today: dict[str, bool] = {}
_proximity_fired: dict[str, bool] = {}
_last_reset_date: date | None = None
_session_data: dict[str, dict] = {}  # {ticker: {high, low}}


def _reset_if_new_day():
    global _fired_today, _proximity_fired, _last_reset_date, _session_data
    today = date.today()
    if _last_reset_date != today:
        _fired_today = {}
        _proximity_fired = {}
        _last_reset_date = today
        _session_data = {}
        logger.info("Stock monitor: new day reset")


def _fetch_price(ticker: str) -> dict | None:
    """Fetch stock price from Yahoo Finance."""
    import json
    import urllib.request
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1m&range=1d"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "PredictorX/1.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        meta = data["chart"]["result"][0]["meta"]
        return {
            "ticker": ticker,
            "price": meta.get("regularMarketPrice", 0),
            "prev_close": meta.get("previousClose", 0) or meta.get("chartPreviousClose", 0),
            "open": meta.get("regularMarketOpen", 0),
            "day_high": meta.get("regularMarketDayHigh", 0),
            "day_low": meta.get("regularMarketDayLow", 0),
        }
    except Exception as e:
        logger.debug(f"{ticker} fetch failed: {e}")
        return None


async def check_stock_levels():
    """
    Main polling function — called every 2 min during market hours.
    Checks all watched stocks against their key levels.
    """
    _reset_if_new_day()

    for ticker, config in WATCHED_STOCKS.items():
        data = _fetch_price(ticker)
        if not data or not data["price"]:
            continue

        price = data["price"]
        prev_close = data["prev_close"]
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close else 0

        # Track session extremes
        if ticker not in _session_data:
            _session_data[ticker] = {"high": price, "low": price}
        if price > _session_data[ticker]["high"]:
            _session_data[ticker]["high"] = price
        if price < _session_data[ticker]["low"]:
            _session_data[ticker]["low"] = price

        levels = config["levels"]
        session = _session_data[ticker]

        # ── Check each level ─────────────────────────────────
        for level_id, level in levels.items():
            if _fired_today.get(level_id):
                continue

            target = level["price"]
            triggered = False

            if level["direction"] == "above" and price >= target:
                triggered = True
            elif level["direction"] == "below" and price <= target:
                triggered = True

            if triggered:
                _fired_today[level_id] = True
                alert = {
                    "alert_type": "stock_level",
                    "ticker": ticker,
                    "level_id": level_id,
                    "level_label": level["label"],
                    "level_price": target,
                    "direction": level["direction"],
                    "action": level["action"],
                    "trade": level["trade"],
                    "price": price,
                    "prev_close": prev_close,
                    "change_pct": change_pct,
                    "session_high": session["high"],
                    "session_low": session["low"],
                    "all_levels": levels,
                }
                await _send_stock_alert(alert)
                logger.warning(
                    f"{ticker} LEVEL: {level['label']} ${target:.0f} — "
                    f"${price:.2f} ({change_pct:+.1f}%)"
                )

        # ── Proximity alerts ──────────────────────────────────
        for level_id, level in levels.items():
            prox_key = f"prox_{level_id}"
            if _proximity_fired.get(prox_key) or _fired_today.get(level_id):
                continue

            target = level["price"]
            distance_pct = abs(price - target) / target * 100

            if distance_pct <= PROXIMITY_PCT:
                _proximity_fired[prox_key] = True
                alert = {
                    "alert_type": "stock_proximity",
                    "ticker": ticker,
                    "level_id": level_id,
                    "level_label": level["label"],
                    "level_price": target,
                    "direction": level["direction"],
                    "action": level["action"],
                    "trade": level["trade"],
                    "price": price,
                    "prev_close": prev_close,
                    "change_pct": change_pct,
                    "distance_pct": distance_pct,
                }
                await _send_stock_alert(alert)
                logger.info(
                    f"{ticker} PROXIMITY: {distance_pct:.1f}% from "
                    f"{level['label']} ${target:.0f}"
                )


async def _send_stock_alert(alert: dict):
    """Format and send stock alert via Telegram with chart."""
    from telegram.bot import get_bot
    from telegram.formatters import format_stock_level_alert

    bot = get_bot()
    if not bot.configured:
        return

    text = format_stock_level_alert(alert)
    await bot.send_message(text)

    # Send chart on level hits (not proximity)
    if alert["alert_type"] == "stock_level":
        ticker = alert["ticker"]
        chart_url = f"https://elite.finviz.com/chart.ashx?t={ticker}&ty=c&ta=1&p=d&s=l"
        caption = f"{ticker} ${alert['price']:.2f} — {alert['level_label']}"
        await bot.send_photo(chart_url, caption=caption)
