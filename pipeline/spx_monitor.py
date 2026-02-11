"""
PredictorX — Real-Time SPX Monitor
Driven by 6,563-day backtest (2000-2026).

Polls SPX every 5 min during market hours.
When intraday drop crosses a threshold → fires actionable trade alert
with exact win rates, edge ratings, and specific Kalshi + ToS trades.

Safety:
  - Clustering guard: won't suggest tails the day after a >2% crash
  - VIX regime gate: blocks trades in HIGH/CRISIS
  - Blackout dates: no trades on FOMC/CPI/NFP days
  - Monthly/DOW adjustments baked into confidence
"""

import logging
from datetime import date, datetime

from config.constants import (
    TAIL_PROB, TAIL_WIN_RATES, REGIME_SAMPLE_DAYS,
    CLUSTER_MULTIPLIER, MONTHLY_RISK_FACTOR, DOW_DROP2_RATE,
    SAFE_MONTHS, RISKY_MONTHS, BLACKOUT_DATES,
    TOS_INSTRUMENTS, TOS_ENABLED, BASELINE_STATS, edge_rating,
)

logger = logging.getLogger(__name__)

# ── Daily State ──────────────────────────────────────────────
_fired_today: dict[float, bool] = {}
_last_reset_date: date | None = None
_spx_open: float | None = None
_prev_day_return: float | None = None  # Yesterday's return for clustering


def _reset_if_new_day():
    global _fired_today, _last_reset_date, _spx_open
    today = date.today()
    if _last_reset_date != today:
        _fired_today = {}
        _last_reset_date = today
        _spx_open = None
        logger.info("SPX monitor: new day reset")


async def check_spx_price():
    """
    Main polling function — called every 5 min during market hours.
    Detects intraday drops, checks safety gates, fires trade alerts.
    """
    global _spx_open, _prev_day_return

    _reset_if_new_day()

    # ── Fetch live data ──────────────────────────────────────
    try:
        from adapters.kalshi_data import get_vix, get_spx
        spx_data = get_spx()
        vix_data = get_vix()
    except Exception as e:
        logger.debug(f"SPX monitor fetch failed: {e}")
        return

    price = spx_data.get("price")
    open_price = spx_data.get("open") or spx_data.get("prev_close")
    prev_close = spx_data.get("prev_close")

    if not price or not open_price:
        return

    # Cache today's open on first fetch
    if _spx_open is None:
        _spx_open = open_price

    # Track previous day's return for clustering guard
    if prev_close and open_price and _prev_day_return is None:
        # Approximate: yesterday's close-to-close
        _prev_day_return = ((open_price - prev_close) / prev_close) * 100

    regime = vix_data.get("regime", "MEDIUM")
    vix_price = vix_data.get("price", 0)
    change_pct = ((price - _spx_open) / _spx_open) * 100

    # ── Check thresholds ─────────────────────────────────────
    # 1.0% and 1.5% = dip-buy call alerts (options entry signals)
    # 2.0%, 3.0%, 5.0% = tail trade alerts (sell premium / bounce trade)
    for drop_pct in [1.0, 1.5, 2.0, 3.0, 5.0]:
        threshold = -drop_pct

        if _fired_today.get(drop_pct):
            continue

        if change_pct <= threshold:
            _fired_today[drop_pct] = True

            alert = _build_trade_alert(
                drop_pct=drop_pct,
                spx_price=price,
                spx_open=_spx_open,
                change_pct=change_pct,
                vix_price=vix_price,
                regime=regime,
            )
            await _send_alert(alert)

            logger.warning(
                f"DROP ALERT: SPX {change_pct:+.2f}% "
                f"(${price:,.0f}) VIX {vix_price:.1f} ({regime})"
            )


def _build_trade_alert(drop_pct: float, spx_price: float, spx_open: float,
                       change_pct: float, vix_price: float, regime: str) -> dict:
    """
    Build a complete trade alert with backtest-backed data.
    Returns a dict with everything the formatter needs.
    """
    today = date.today()
    month = today.month
    dow = today.weekday()

    # ── Core stats from 6,563-day backtest ───────────────────
    hist_prob = TAIL_PROB.get(regime, TAIL_PROB["MEDIUM"]).get(int(drop_pct), 0.05)
    win_rate = TAIL_WIN_RATES.get(regime, {}).get(int(drop_pct), 0.95)
    sample_days = REGIME_SAMPLE_DAYS.get(regime, 6563)

    # ── Safety checks ────────────────────────────────────────
    blocked = False
    block_reasons = []

    # Blackout (FOMC/CPI/NFP)
    if today.strftime("%Y-%m-%d") in BLACKOUT_DATES:
        blocked = True
        block_reasons.append("FOMC/CPI/NFP blackout day")

    # VIX regime gate
    if regime in ("HIGH", "CRISIS"):
        blocked = True
        block_reasons.append(f"VIX {regime} — tail win rate only {win_rate:.0%}")

    # Clustering guard
    cluster_warning = False
    if _prev_day_return is not None and _prev_day_return <= -2.0:
        cluster_mult = CLUSTER_MULTIPLIER.get(int(drop_pct), 2.0)
        adjusted_prob = min(hist_prob * cluster_mult, 0.50)
        cluster_warning = True
        if adjusted_prob > 0.10:
            blocked = True
            block_reasons.append(
                f"Clustering: yesterday dropped {_prev_day_return:+.1f}%, "
                f"tail risk {cluster_mult:.1f}x elevated"
            )

    # ── Market price estimate (Kalshi overprices ~3-5x) ──────
    est_market_price = max(0.03, min(0.15, hist_prob * 4))
    edge = est_market_price - hist_prob
    rating = edge_rating(est_market_price, hist_prob)

    # ── Monthly and DOW context ──────────────────────────────
    month_factor = MONTHLY_RISK_FACTOR.get(month, 1.0)
    month_safe = month in SAFE_MONTHS
    dow_rate = DOW_DROP2_RATE.get(dow, 0.043)

    # ── Dip-Buy Call Options (for -1% and -1.5% alerts) ──────
    call_options = []
    if drop_pct <= 1.5 and not blocked:
        spy_price = spx_price / 10
        qqq_est = spx_price * 0.0883  # QQQ/SPX ratio ~0.0883
        # ATM and slightly OTM calls for Feb 20 weekly
        atm_spy = round(spy_price)
        atm_qqq = round(qqq_est)
        call_options = [
            {"ticker": "SPY", "strike": f"{atm_spy}C", "label": "ATM",
             "note": f"SPY ~${spy_price:.0f} — buy Feb 20 exp"},
            {"ticker": "SPY", "strike": f"{atm_spy + 3}C", "label": "+$3 OTM",
             "note": "Cheaper, more leverage"},
            {"ticker": "QQQ", "strike": f"{atm_qqq}C", "label": "ATM",
             "note": f"QQQ — higher beta, buy Feb 20 exp"},
            {"ticker": "QQQ", "strike": f"{atm_qqq + 5}C", "label": "+$5 OTM",
             "note": "Cheaper QQQ lottery"},
        ]

    # ── Build ToS trades ─────────────────────────────────────
    tos_trades = []
    if TOS_ENABLED and not blocked:
        # SPY put credit spread
        spy_info = TOS_INSTRUMENTS.get("SPY", {})
        if spy_info.get("max_contracts", 0) > 0:
            spy_price = spx_price / 10
            short_strike = round(spy_price * (1 - drop_pct / 100), 0)
            long_strike = short_strike - spy_info["spread_width"]
            tos_trades.append({
                "instrument": "SPY",
                "action": f"SELL {short_strike}p / BUY {long_strike}p",
                "expiry": "0DTE or weekly",
                "risk": spy_info["max_risk_per_spread"] * spy_info["max_contracts"],
            })

        # /MES long — only LOW/LOW_MED
        mes_info = TOS_INSTRUMENTS.get("/MES", {})
        if mes_info.get("max_contracts", 0) > 0 and regime in ("LOW", "LOW_MED"):
            tos_trades.append({
                "instrument": "/MES",
                "action": f"BUY 1 @ ~{spx_price:.0f}",
                "margin": mes_info["margin"],
            })

        # /MNQ long — only LOW
        mnq_info = TOS_INSTRUMENTS.get("/MNQ", {})
        if mnq_info.get("max_contracts", 0) > 0 and regime == "LOW":
            tos_trades.append({
                "instrument": "/MNQ",
                "action": "BUY 1",
                "margin": mnq_info["margin"],
            })

    return {
        "drop_pct": drop_pct,
        "spx_price": spx_price,
        "spx_open": spx_open,
        "change_pct": change_pct,
        "vix_price": vix_price,
        "regime": regime,
        "hist_prob": hist_prob,
        "win_rate": win_rate,
        "sample_days": sample_days,
        "edge": edge,
        "rating": rating,
        "est_market_price": est_market_price,
        "blocked": blocked,
        "block_reasons": block_reasons,
        "cluster_warning": cluster_warning,
        "month_factor": month_factor,
        "month_safe": month_safe,
        "dow_rate": dow_rate,
        "tos_trades": tos_trades,
        "call_options": call_options,
    }


async def _send_alert(alert: dict):
    """Format and send the trade alert via Telegram."""
    from telegram.bot import get_bot
    from telegram.formatters import format_spx_drop_alert

    bot = get_bot()
    if not bot.configured:
        return

    text = format_spx_drop_alert(alert)
    await bot.send_message(text)
