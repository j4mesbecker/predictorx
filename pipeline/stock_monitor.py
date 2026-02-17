"""
PredictorX — Stock Level Monitor
Polls individual stocks every 2 min during market hours (9:30 AM - 4 PM ET).
Fires Telegram alerts when key technical levels are hit or approached.

Currently tracking:
  SPY  — S&P 500 ETF ($650 support → $675 demand → $698 ATH → $700/$720 breakout)
  QQQ  — Nasdaq 100 ETF ($580 support → $595 demand → $637 ATH → $650 breakout)
  TSLA — Breakout play ($363 support → $441 breakout → $500/$572/$700 targets)
  NVDA — Supply/demand zones ($171 demand → $194 supply → $212 ATH)
  PLTR — Momentum pullback ($120 demand → $150 pivot → $187 supply → $207 ATH)

Alerts fire once per level per day. Proximity alerts (within 2%) fire separately.
Each level-hit alert includes a Finviz chart image.

Options trading on ThinkorSwim:
  - All trades use naked calls/puts (buying options only)
  - Minimum 14 DTE to avoid theta burn
  - Entry window: 8:30 AM - 3:30 PM CST
  - Best entry: first hour after open (8:30-9:30 AM CST)
"""

import logging
from datetime import date

logger = logging.getLogger(__name__)

# ── SPY Key Levels (S&P 500 ETF) ───────────────────────────
# Current: ~$681 | SMA20: $690 | SMA50: $687 | ATH: $698
SPY_LEVELS = {
    "SPY_SUPPORT_650": {
        "price": 650.0,
        "label": "3m Low Support",
        "direction": "below",
        "action": "MAJOR SUPPORT — 3-month low. Market-wide selloff if lost.",
        "trade": "Do NOT buy calls yet. Wait for VIX reversion signal. Cash is a position.",
    },
    "SPY_DEMAND_675": {
        "price": 675.0,
        "label": "30d Low Demand",
        "direction": "below",
        "action": "DEMAND ZONE — 30-day low being tested. Buyers expected here.",
        "trade": "BUY SPY calls 14+ DTE at current strike. Wait for CPI/catalyst to clear.",
        "options_zone": "demand",
    },
    "SPY_SMA50_687": {
        "price": 687.0,
        "label": "50-Day SMA",
        "direction": "above",
        "action": "ABOVE 50 SMA — Trend structure intact. Bullish.",
        "trade": "BUY SPY calls ATM 14+ DTE. Trend following entry.",
    },
    "SPY_ATH_698": {
        "price": 698.0,
        "label": "All-Time High",
        "direction": "above",
        "action": "ATH TEST — $698 is the ceiling. Watch for rejection or breakout.",
        "trade": "If breakout: BUY SPY 710C 30+ DTE. If rejection: wait for retest.",
        "options_zone": "supply",
    },
    "SPY_BREAKOUT_700": {
        "price": 700.0,
        "label": "$700 Psychological",
        "direction": "above",
        "action": "$700 CLEARED — Psychological breakout. Momentum accelerates.",
        "trade": "BUY SPY 720C 30+ DTE. Trail stops. Let it run.",
    },
    "SPY_TARGET_720": {
        "price": 720.0,
        "label": "Upside Target",
        "direction": "above",
        "action": "$720 TARGET — Extended move. Take profits on swing positions.",
        "trade": "Sell 75% of calls. Keep small runner for $740.",
    },
}

# ── QQQ Key Levels (Nasdaq 100 ETF) ────────────────────────
# Current: ~$601 | SMA20: $617 | SMA50: $619 | ATH: $637
QQQ_LEVELS = {
    "QQQ_SUPPORT_580": {
        "price": 580.0,
        "label": "3m Low Support",
        "direction": "below",
        "action": "MAJOR SUPPORT — 3-month low. Tech selloff accelerating.",
        "trade": "Do NOT buy calls. Consider QQQ puts if VIX > 25. Cash.",
    },
    "QQQ_DEMAND_595": {
        "price": 595.0,
        "label": "30d Low Demand",
        "direction": "below",
        "action": "DEMAND ZONE — 30-day low. Tech buyers step in here historically.",
        "trade": "BUY QQQ calls ATM 14+ DTE if VIX < 25. Higher beta than SPY.",
        "options_zone": "demand",
    },
    "QQQ_SMA50_619": {
        "price": 619.0,
        "label": "50-Day SMA",
        "direction": "above",
        "action": "ABOVE 50 SMA — Tech trend intact. Resume longs.",
        "trade": "BUY QQQ calls ATM 14+ DTE. Tech leading again.",
    },
    "QQQ_ATH_637": {
        "price": 637.0,
        "label": "All-Time High",
        "direction": "above",
        "action": "ATH BREAKOUT — All-time high cleared. Blue sky.",
        "trade": "BUY QQQ 650C 30+ DTE. Momentum trade into breakout.",
        "options_zone": "supply",
    },
    "QQQ_TARGET_650": {
        "price": 650.0,
        "label": "$650 Target",
        "direction": "above",
        "action": "$650 HIT — Extended target reached.",
        "trade": "Sell 75% of calls. Keep runner for $670. Tighten stops.",
    },
}

# ── TSLA Key Levels (TrendSpider / EliteOptionsTrader) ──────
# Current: ~$417 | SMA20: $426 | SMA50: $444 | ATH: $499
TSLA_LEVELS = {
    "TSLA_SUPPORT_363": {
        "price": 363.0,
        "label": "Support Floor",
        "direction": "below",
        "action": "DANGER — September breakout level. If lost, next support $290.",
        "trade": "Close all TSLA longs. Do NOT buy calls.",
    },
    "TSLA_DEMAND_388": {
        "price": 388.0,
        "label": "30d Low Demand",
        "direction": "below",
        "action": "30-DAY LOW — Recent floor being tested. Bounce zone.",
        "trade": "BUY TSLA calls 30+ DTE at current strike if VIX < 25.",
        "options_zone": "demand",
    },
    "TSLA_BREAKOUT_441": {
        "price": 441.0,
        "label": "Breakout Trigger",
        "direction": "above",
        "action": "BREAKOUT — 4-month consolidation resolved upward.",
        "trade": "BUY TSLA 3/20 500C. Target $500 on momentum.",
        "options_zone": "supply",
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
# Current: ~$187 | SMA20: $186 | SMA50: $184 | ATH: $212
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
        "options_zone": "demand",
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
        "options_zone": "supply",
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

# ── PLTR Key Levels (Palantir Technologies) ─────────────────
# Current: ~$129 | SMA20: $153 | SMA50: $171 | ATH: $207
# Massive pullback from $207 ATH — momentum stock, volatile
PLTR_LEVELS = {
    "PLTR_SUPPORT_120": {
        "price": 120.0,
        "label": "Pullback Support",
        "direction": "below",
        "action": "SUPPORT TEST — Below recent 30d low. Deep pullback from $207.",
        "trade": "Wait for stabilization. Do NOT catch falling knife. Watch $100.",
    },
    "PLTR_DEMAND_100": {
        "price": 100.0,
        "label": "$100 Psychological",
        "direction": "below",
        "action": "$100 BROKEN — Psychological support lost. Major correction.",
        "trade": "If VIX < 25 and stabilizing: BUY PLTR calls 45+ DTE. High risk.",
    },
    "PLTR_PIVOT_150": {
        "price": 150.0,
        "label": "SMA20 / Pivot",
        "direction": "above",
        "action": "ABOVE SMA20 — Reclaiming trend. Bounce from pullback.",
        "trade": "BUY PLTR calls 30+ DTE. Target $170 SMA50 reclaim.",
    },
    "PLTR_SMA50_171": {
        "price": 171.0,
        "label": "50-Day SMA",
        "direction": "above",
        "action": "ABOVE 50 SMA — Full trend recovery. Strength confirmed.",
        "trade": "BUY PLTR 200C 45+ DTE. Momentum rebuilding toward ATH.",
    },
    "PLTR_SUPPLY_187": {
        "price": 187.0,
        "label": "Prior Consolidation",
        "direction": "above",
        "action": "SUPPLY CLEARED — Prior congestion zone broken.",
        "trade": "Add to position. PLTR 220C 45+ DTE. ATH retest incoming.",
    },
    "PLTR_ATH_207": {
        "price": 207.0,
        "label": "All-Time High",
        "direction": "above",
        "action": "ATH BREAKOUT — All-time high cleared. Parabolic potential.",
        "trade": "Trail stops. Let it run. Consider 250C lottos. Take partials at $230.",
    },
}

# All tracked stocks
WATCHED_STOCKS = {
    "SPY": {"levels": SPY_LEVELS, "chart_ticker": "SPY"},
    "QQQ": {"levels": QQQ_LEVELS, "chart_ticker": "QQQ"},
    "TSLA": {"levels": TSLA_LEVELS, "chart_ticker": "TSLA"},
    "NVDA": {"levels": NVDA_LEVELS, "chart_ticker": "NVDA"},
    "PLTR": {"levels": PLTR_LEVELS, "chart_ticker": "PLTR"},
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
    from pipeline.tasks import is_market_open_today
    if not is_market_open_today():
        return

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

                # ── Options signal for demand/supply zones ────────
                options_zone = level.get("options_zone")
                if options_zone:
                    try:
                        from adapters.kalshi_data import get_vix
                        vix_data = get_vix()
                        vix_price = vix_data.get("price", 18)
                        regime = vix_data.get("regime", "MEDIUM")
                    except Exception:
                        vix_price, regime = 18.0, "MEDIUM"

                    try:
                        from core.strategies.options_strategy import (
                            compute_naked_put_signal,
                            compute_naked_call_signal,
                        )
                        if options_zone == "demand":
                            alert["options_signal"] = compute_naked_put_signal(
                                ticker=ticker,
                                current_price=price,
                                vix_price=vix_price,
                                regime=regime,
                                trigger_type="demand_zone",
                                drop_pct=abs(change_pct) if change_pct < 0 else 0,
                            )
                        elif options_zone == "supply":
                            alert["options_signal"] = compute_naked_call_signal(
                                ticker=ticker,
                                current_price=price,
                                vix_price=vix_price,
                                regime=regime,
                                trigger_type="supply_zone",
                            )
                    except Exception as e:
                        logger.debug(f"Options signal error for {level_id}: {e}")

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

                # ── Options signal preview on proximity ───────────
                options_zone = level.get("options_zone")
                if options_zone:
                    try:
                        from adapters.kalshi_data import get_vix
                        vix_data = get_vix()
                        vix_price = vix_data.get("price", 18)
                        regime = vix_data.get("regime", "MEDIUM")
                    except Exception:
                        vix_price, regime = 18.0, "MEDIUM"

                    try:
                        from core.strategies.options_strategy import (
                            compute_naked_put_signal,
                            compute_naked_call_signal,
                        )
                        if options_zone == "demand":
                            alert["options_signal"] = compute_naked_put_signal(
                                ticker=ticker,
                                current_price=price,
                                vix_price=vix_price,
                                regime=regime,
                                trigger_type="demand_zone",
                                drop_pct=abs(change_pct) if change_pct < 0 else 0,
                            )
                        elif options_zone == "supply":
                            alert["options_signal"] = compute_naked_call_signal(
                                ticker=ticker,
                                current_price=price,
                                vix_price=vix_price,
                                regime=regime,
                                trigger_type="supply_zone",
                            )
                    except Exception as e:
                        logger.debug(f"Options signal error for {level_id}: {e}")

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
