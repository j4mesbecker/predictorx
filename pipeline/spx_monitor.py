"""
PredictorX — Real-Time SPX Monitor
Driven by 6,563-day backtest (2000-2026).

Polls SPX every 5 min during market hours.
Three alert types:
  1. DIP BUY (-1%, -1.5%) — call options at the DIP price, min 14 DTE
  2. TAIL TRADE (-2%, -3%, -5%) — premium selling + bounce trades
  3. VIX REVERSION — VIX spikes above 20 then drops back below 19
     (highest-conviction bounce entry from backtest)

Safety:
  - Clustering guard: blocks dip buys after any -0.8% day (catches multi-day selloffs)
  - VIX regime gate: blocks trades in HIGH/CRISIS
  - Blackout dates: no trades on FOMC/CPI/NFP days
  - Monthly/DOW adjustments baked into confidence
"""

import logging
from datetime import date, datetime, timedelta

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

# ── VIX Reversion State ─────────────────────────────────────
_vix_peak_today: float = 0.0          # Track intraday VIX high
_vix_crossed_above_20: bool = False   # Has VIX gone above 20 today?
_vix_reversion_fired: bool = False    # Only fire once per day


def _reset_if_new_day():
    global _fired_today, _last_reset_date, _spx_open
    global _vix_peak_today, _vix_crossed_above_20, _vix_reversion_fired
    today = date.today()
    if _last_reset_date != today:
        _fired_today = {}
        _last_reset_date = today
        _spx_open = None
        _vix_peak_today = 0.0
        _vix_crossed_above_20 = False
        _vix_reversion_fired = False
        logger.info("SPX monitor: new day reset")


async def check_spx_price():
    """
    Main polling function — called every 5 min during market hours.
    Detects intraday drops, checks safety gates, fires trade alerts.
    Also monitors VIX for regime transition (spike + reversion) signals.
    """
    global _spx_open, _prev_day_return
    global _vix_peak_today, _vix_crossed_above_20, _vix_reversion_fired

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

    # ── VIX Reversion Detection ────────────────────────────────
    # Track VIX intraday peak; fire alert when VIX spikes >20 then drops <19
    # This caught the Feb 6 bounce perfectly in backtest
    if vix_price > _vix_peak_today:
        _vix_peak_today = vix_price
    if vix_price >= 20.0:
        _vix_crossed_above_20 = True

    if (_vix_crossed_above_20
            and not _vix_reversion_fired
            and vix_price < 19.0
            and _vix_peak_today >= 20.0):
        _vix_reversion_fired = True
        alert = _build_vix_reversion_alert(
            spx_price=price,
            spx_open=_spx_open,
            change_pct=change_pct,
            vix_price=vix_price,
            vix_peak=_vix_peak_today,
            regime=regime,
        )
        await _send_alert(alert)
        logger.warning(
            f"VIX REVERSION: VIX peaked {_vix_peak_today:.1f} → now {vix_price:.1f} "
            f"SPX {change_pct:+.2f}%"
        )

    # ── Check drop thresholds ──────────────────────────────────
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

    # Clustering guard — TIGHTENED from -2.0% to -0.8%
    # Feb 3-5 backtest showed: -0.97% day followed by -0.60%, then -0.57%
    # The old -2% threshold missed this multi-day selloff
    cluster_warning = False
    cluster_threshold = -0.8 if drop_pct <= 1.5 else -2.0  # Tighter for dip buys
    if _prev_day_return is not None and _prev_day_return <= cluster_threshold:
        cluster_mult = CLUSTER_MULTIPLIER.get(int(drop_pct), 2.0)
        adjusted_prob = min(hist_prob * cluster_mult, 0.50)
        cluster_warning = True
        if drop_pct <= 1.5:
            # For dip buys: always block after a red day >0.8%
            # This prevents catching falling knives (Feb 3→4→5 pattern)
            blocked = True
            block_reasons.append(
                f"Back-to-back drop: yesterday {_prev_day_return:+.1f}% — "
                f"wait for VIX reversion signal instead"
            )
        elif adjusted_prob > 0.10:
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
    # FIXED: Use CURRENT dip price for strikes (not open price)
    # FIXED: Dynamic expiry — minimum 14 DTE (weeklies die on multi-day drops)
    call_options = []
    if drop_pct <= 1.5 and not blocked:
        spy_price = spx_price / 10  # Current SPY price at the dip
        qqq_est = spx_price * 0.0883  # QQQ/SPX ratio ~0.0883

        # Dynamic expiry: find next Friday that's at least 14 days out
        min_exp = today + timedelta(days=14)
        days_to_friday = (4 - min_exp.weekday()) % 7
        exp_date = min_exp + timedelta(days=days_to_friday)
        exp_str = exp_date.strftime("%b %d")

        # Strikes based on CURRENT dip price (not open)
        atm_spy = round(spy_price)
        atm_qqq = round(qqq_est)
        call_options = [
            {"ticker": "SPY", "strike": f"{atm_spy}C", "label": "ATM at dip",
             "note": f"SPY ~${spy_price:.0f} now — {exp_str} exp (14+ DTE)"},
            {"ticker": "SPY", "strike": f"{atm_spy + 3}C", "label": "+$3 OTM",
             "note": f"Cheaper, more leverage — {exp_str} exp"},
            {"ticker": "QQQ", "strike": f"{atm_qqq}C", "label": "ATM at dip",
             "note": f"QQQ ~${qqq_est:.0f} now — {exp_str} exp (14+ DTE)"},
            {"ticker": "QQQ", "strike": f"{atm_qqq + 5}C", "label": "+$5 OTM",
             "note": f"Cheaper QQQ — {exp_str} exp"},
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


def _build_vix_reversion_alert(spx_price: float, spx_open: float,
                               change_pct: float, vix_price: float,
                               vix_peak: float, regime: str) -> dict:
    """
    Build a VIX reversion alert — highest-conviction bounce signal.
    Fires when VIX spikes above 20 then drops back below 19.
    Feb 5-6 backtest: VIX 21.8→20.4, SPY bounced +1.34%, QQQ +1.58%.
    """
    today = date.today()
    spy_price = spx_price / 10
    qqq_est = spx_price * 0.0883

    # Dynamic expiry: minimum 14 DTE
    min_exp = today + timedelta(days=14)
    days_to_friday = (4 - min_exp.weekday()) % 7
    exp_date = min_exp + timedelta(days=days_to_friday)
    exp_str = exp_date.strftime("%b %d")

    # Strikes at current price (the bottom)
    atm_spy = round(spy_price)
    atm_qqq = round(qqq_est)

    call_options = [
        {"ticker": "SPY", "strike": f"{atm_spy}C", "label": "ATM",
         "note": f"SPY ~${spy_price:.0f} — {exp_str} exp (14+ DTE)"},
        {"ticker": "SPY", "strike": f"{atm_spy + 3}C", "label": "+$3 OTM",
         "note": f"More leverage — {exp_str} exp"},
        {"ticker": "QQQ", "strike": f"{atm_qqq}C", "label": "ATM",
         "note": f"QQQ ~${qqq_est:.0f} — higher beta — {exp_str} exp"},
        {"ticker": "QQQ", "strike": f"{atm_qqq + 5}C", "label": "+$5 OTM",
         "note": f"Cheaper QQQ — {exp_str} exp"},
    ]

    blocked = False
    block_reasons = []
    if today.strftime("%Y-%m-%d") in BLACKOUT_DATES:
        blocked = True
        block_reasons.append("FOMC/CPI/NFP blackout day")

    return {
        "alert_type": "vix_reversion",
        "spx_price": spx_price,
        "spx_open": spx_open,
        "change_pct": change_pct,
        "vix_price": vix_price,
        "vix_peak": vix_peak,
        "regime": regime,
        "call_options": call_options,
        "exp_str": exp_str,
        "blocked": blocked,
        "block_reasons": block_reasons,
    }


async def _send_alert(alert: dict):
    """Format, send the trade alert via Telegram, and track positions for exit monitoring."""
    from telegram.bot import get_bot
    from telegram.formatters import format_spx_drop_alert, format_vix_reversion_alert
    from telegram.scheduled_alerts import track_position

    bot = get_bot()
    if not bot.configured:
        return

    if alert.get("alert_type") == "vix_reversion":
        text = format_vix_reversion_alert(alert)
    else:
        text = format_spx_drop_alert(alert)
    await bot.send_message(text)

    # Track position for exit signal monitoring (only non-blocked trades)
    if not alert.get("blocked"):
        spy_price = alert["spx_price"] / 10
        call_options = alert.get("call_options", [])

        if alert.get("alert_type") == "vix_reversion":
            # VIX reversion = high conviction, track with wider targets
            for c in call_options[:2]:  # Track top 2 suggestions
                track_position(
                    ticker=c["ticker"],
                    strike=c["strike"],
                    entry_price=0,  # Estimated, user sets actual
                    spy_at_entry=spy_price,
                    target_pct=75.0,  # Higher target for high-conviction
                    stop_pct=-40.0,
                )
        elif alert.get("drop_pct", 0) <= 1.5:
            # Dip buy calls — standard targets
            for c in call_options[:2]:
                track_position(
                    ticker=c["ticker"],
                    strike=c["strike"],
                    entry_price=0,
                    spy_at_entry=spy_price,
                    target_pct=50.0,
                    stop_pct=-50.0,
                )
