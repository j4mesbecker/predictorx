"""
PredictorX — Options Trading Strategy & Psychology Framework

Core computation module for naked put and naked call signals.
Every function returns a fully-formed signal dict that includes:
  - Trade details (ticker, strike, expiry, premium estimate)
  - Risk management (max loss, position size, contracts)
  - Psychology framework (entry reason, exit plan, psychology note)
  - Conviction grade (A+ to C)

Called by: spx_monitor.py, stock_monitor.py, tasks.py, scheduled_alerts.py
Depends on: config/constants.py (OPTIONS_* constants)

All times in CST.
"""

import logging
from datetime import date, timedelta
from math import sqrt

from config.constants import (
    BLACKOUT_DATES,
    OPTIONS_EXIT_RULES,
    OPTIONS_GRADE_SIZING,
    OPTIONS_MAX_RISK_PER_TRADE,
    OPTIONS_MIN_DTE,
    OPTIONS_MAX_DTE,
    OPTIONS_MIN_RISK_PER_TRADE,
    OPTIONS_OTM_PCT,
    OPTIONS_PREFERRED_DTE,
    OPTIONS_REGIME_SIZING,
    OPTIONS_STRIKE_INCREMENTS,
    OPTIONS_TYPICAL_IV,
    PSYCH_CASH_IS_POSITION,
    PSYCH_CUT_LOSER,
    PSYCH_HOLD_WINNER,
    PSYCH_NO_REVENGE,
    PSYCH_SIZE_CHECK,
    PSYCH_SYSTEM_TRUST,
    PSYCH_THETA_FRIEND,
)

logger = logging.getLogger(__name__)


# ── Helpers ──────────────────────────────────────────────────


def _round_to_strike(price: float, ticker: str) -> float:
    """Round price DOWN to nearest valid strike increment."""
    inc = OPTIONS_STRIKE_INCREMENTS.get(ticker, 1)
    return round(int(price / inc) * inc, 2)


def _round_to_strike_up(price: float, ticker: str) -> float:
    """Round price UP to nearest valid strike increment."""
    inc = OPTIONS_STRIKE_INCREMENTS.get(ticker, 1)
    return round(int(price / inc + 1) * inc, 2)


def _next_weekly_expiry(ref_date: date = None) -> tuple[date, int, str]:
    """
    Find the next Friday expiry with DTE between OPTIONS_MIN_DTE and OPTIONS_MAX_DTE.
    Returns (expiry_date, dte, label like "Feb 21").
    """
    today = ref_date or date.today()
    # Walk forward to find Friday in range
    for d in range(OPTIONS_MIN_DTE, OPTIONS_MAX_DTE + 1):
        candidate = today + timedelta(days=d)
        if candidate.weekday() == 4:  # Friday
            label = candidate.strftime("%b %d")
            return candidate, d, label

    # Fallback: next Friday after min DTE
    min_date = today + timedelta(days=OPTIONS_MIN_DTE)
    days_to_fri = (4 - min_date.weekday()) % 7
    if days_to_fri == 0:
        days_to_fri = 7
    exp = min_date + timedelta(days=days_to_fri)
    dte = (exp - today).days
    return exp, dte, exp.strftime("%b %d")


def _estimate_premium(
    current_price: float,
    strike: float,
    vix: float,
    dte: int,
    option_type: str = "put",
) -> float:
    """
    Rough premium estimate using VIX-implied volatility.
    This is an approximation — user must verify on TOS.

    For OTM options: premium ~ price * sigma * sqrt(T) * delta_approx
    where sigma = VIX/100 (annualized), T = dte/365
    delta_approx is based on how far OTM we are.
    """
    if vix <= 0 or dte <= 0 or current_price <= 0:
        return 0.50  # safe default

    sigma = vix / 100.0
    time_factor = sqrt(dte / 365.0)
    atm_premium = current_price * sigma * time_factor

    # OTM distance factor: deeper OTM = less premium
    distance_pct = abs(current_price - strike) / current_price
    if distance_pct <= 0.01:
        delta_factor = 0.45  # near ATM
    elif distance_pct <= 0.03:
        delta_factor = 0.25  # slightly OTM
    elif distance_pct <= 0.05:
        delta_factor = 0.12  # moderately OTM
    elif distance_pct <= 0.08:
        delta_factor = 0.06  # far OTM
    else:
        delta_factor = 0.03  # very far OTM

    premium = atm_premium * delta_factor
    # Round to nearest 0.05
    premium = round(premium * 20) / 20
    return max(0.15, premium)  # Floor at $0.15


def _compute_conviction_grade(
    trigger_type: str,
    regime: str,
    brando_alignment: bool = False,
    bracket_alignment: bool = False,
) -> str:
    """
    Compute A+/A/B/C conviction grade.

    Trigger hierarchy:
      vix_reversion = A+ (highest conviction from backtest)
      spx_dip + LOW/LOW_MED = A
      demand_zone + brando confirmation = A
      resistance_zone = B
      daily_intel = B/C

    Regime adjustments:
      LOW/LOW_MED = no change
      MEDIUM = downgrade 1 step
      HIGH = downgrade 2 steps (puts only)
      CRISIS = F (blocked)
    """
    # Base grade from trigger
    trigger_grades = {
        "vix_reversion": "A+",
        "spx_dip": "A",
        "demand_zone": "A",
        "resistance_zone": "B",
        "bracket_resistance": "B",
        "daily_intel": "B",
        "daily_intel_weak": "C",
    }
    base = trigger_grades.get(trigger_type, "C")

    # Boost for confirmations
    if brando_alignment and base in ("B", "C"):
        base = "A" if base == "B" else "B"
    if bracket_alignment and base in ("B", "C"):
        base = "A" if base == "B" else "B"

    # Regime downgrade
    grade_order = ["A+", "A", "B", "C"]
    idx = grade_order.index(base) if base in grade_order else 3

    if regime == "MEDIUM":
        idx = min(idx + 1, 3)
    elif regime == "HIGH":
        idx = min(idx + 2, 3)
    elif regime == "CRISIS":
        return "F"

    return grade_order[idx]


def _is_blocked(regime: str, today: date = None) -> tuple[bool, list[str]]:
    """Check if options trading is blocked today."""
    today = today or date.today()
    today_str = today.strftime("%Y-%m-%d")
    reasons = []

    if regime == "CRISIS":
        reasons.append("VIX CRISIS regime — ALL CASH, no options trades")
    if today_str in BLACKOUT_DATES:
        reasons.append("FOMC/CPI/NFP day — no new naked positions")

    return bool(reasons), reasons


def _get_max_risk(grade: str, regime: str) -> float:
    """Compute max risk for a trade given grade and regime."""
    regime_frac = OPTIONS_REGIME_SIZING.get(regime, {}).get("risk_frac", 0.5)
    grade_frac = OPTIONS_GRADE_SIZING.get(grade, {}).get("size_frac", 0.25)

    max_risk = OPTIONS_MAX_RISK_PER_TRADE * regime_frac * grade_frac
    return max(OPTIONS_MIN_RISK_PER_TRADE, round(max_risk, -1))  # Round to $10


def _psychology_note(trigger_type: str, is_winner: bool = False) -> str:
    """Select the right psychology message for the context."""
    if is_winner:
        return PSYCH_HOLD_WINNER
    if trigger_type == "vix_reversion":
        return PSYCH_SYSTEM_TRUST
    if trigger_type in ("spx_dip", "demand_zone"):
        return PSYCH_THETA_FRIEND
    if trigger_type in ("resistance_zone", "bracket_resistance"):
        return PSYCH_THETA_FRIEND
    return PSYCH_CASH_IS_POSITION


# ── Main Signal Functions ────────────────────────────────────


def compute_naked_put_signal(
    ticker: str,
    current_price: float,
    vix_price: float,
    regime: str,
    trigger_type: str,
    drop_pct: float = 0.0,
    brando_levels: list = None,
    bracket_alignment: bool = False,
) -> dict:
    """
    Compute a full naked put recommendation.

    Args:
        ticker: SPY, QQQ, NVDA, TSLA, etc.
        current_price: current stock/ETF price
        vix_price: current VIX level
        regime: VIX regime string
        trigger_type: what caused this signal
        drop_pct: intraday drop % (for spx_dip triggers)
        brando_levels: list of Brando level dicts for this ticker
        bracket_alignment: whether bracket data supports this direction

    Returns:
        dict with action, ticker, strike, expiry, premium, risk, grade,
        entry reason, exit plan, psychology note
    """
    today = date.today()

    # ── Block check ─────────────────────────────────────────
    blocked, block_reasons = _is_blocked(regime, today)
    if blocked:
        return {
            "action": "NO TRADE",
            "blocked": True,
            "block_reasons": block_reasons,
        }

    # ── Brando alignment ────────────────────────────────────
    brando_hit = False
    brando_note = ""
    if brando_levels:
        for lvl in brando_levels:
            if lvl.get("ticker", "").upper() != ticker.upper():
                continue
            lvl_type = lvl.get("type", "")
            lvl_price = lvl.get("price", 0)
            if lvl_type in ("support", "demand_zone") and lvl_price > 0:
                distance_pct = abs(current_price - lvl_price) / current_price
                if distance_pct < 0.03:  # Within 3% of Brando level
                    brando_hit = True
                    brando_note = f"Brando {lvl_type} at ${lvl_price:,.0f}"
                    break

    # ── Conviction grade ────────────────────────────────────
    grade = _compute_conviction_grade(
        trigger_type, regime, brando_hit, bracket_alignment,
    )
    if grade == "F":
        return {"action": "NO TRADE", "blocked": True, "block_reasons": ["Grade F"]}

    # ── Strike selection ────────────────────────────────────
    # Sell put below current price (OTM)
    otm_price = current_price * (1 - OPTIONS_OTM_PCT)
    strike = _round_to_strike(otm_price, ticker)

    # For demand zone triggers, use the demand level as strike if sensible
    if trigger_type == "demand_zone" and brando_levels:
        for lvl in brando_levels:
            if (lvl.get("ticker", "").upper() == ticker.upper()
                    and lvl.get("type") in ("support", "demand_zone")):
                candidate = _round_to_strike(lvl["price"], ticker)
                if candidate < current_price:
                    strike = candidate
                    break

    # ── Expiry ──────────────────────────────────────────────
    exp_date, dte, exp_label = _next_weekly_expiry(today)

    # ── Premium estimate ────────────────────────────────────
    iv = OPTIONS_TYPICAL_IV.get(ticker, 0.20)
    # Use live VIX to scale typical IV: if VIX is elevated, premiums are richer
    vix_scale = max(0.7, min(2.0, vix_price / 18.0))
    effective_iv = iv * vix_scale
    premium = _estimate_premium(current_price, strike, effective_iv * 100, dte, "put")

    # ── Position sizing ─────────────────────────────────────
    max_risk = _get_max_risk(grade, regime)
    # For naked puts at this account size: 1 contract
    contracts = 1

    # ── Exit levels ─────────────────────────────────────────
    profit_target = round(premium * 0.50, 2)  # Buy back at 50% of sold premium
    stop_loss = round(premium * OPTIONS_EXIT_RULES["stop_loss_multiplier"], 2)

    # ── Entry reason ────────────────────────────────────────
    reason_parts = []
    if trigger_type == "vix_reversion":
        reason_parts.append(f"VIX reversion: peaked >20, now {vix_price:.1f}")
        reason_parts.append("Highest-conviction bounce signal from backtest")
    elif trigger_type == "spx_dip":
        reason_parts.append(f"SPX dipped {drop_pct:.1f}% in {regime} VIX regime")
        bounce_rates = {"LOW": 98, "LOW_MED": 98, "MEDIUM": 95}
        br = bounce_rates.get(regime, 90)
        reason_parts.append(f"{br}% bounce rate from 6,563-day backtest")
    elif trigger_type == "demand_zone":
        reason_parts.append(f"{ticker} at demand zone ${strike:,.0f}")
        if brando_note:
            reason_parts.append(brando_note)
    else:
        reason_parts.append(f"{ticker} sell put setup at {regime} VIX")

    if brando_hit and trigger_type != "demand_zone":
        reason_parts.append(brando_note)
    if bracket_alignment:
        reason_parts.append("Bracket data confirms support below")

    entry_reason = ". ".join(reason_parts)

    # ── Size label ──────────────────────────────────────────
    grade_info = OPTIONS_GRADE_SIZING.get(grade, {})
    regime_info = OPTIONS_REGIME_SIZING.get(regime, {})
    size_label = f"{grade_info.get('label', '')} | {regime_info.get('label', '')}"

    return {
        "action": "SELL PUT",
        "ticker": ticker,
        "strike": strike,
        "expiry_date": exp_date.isoformat(),
        "expiry_label": exp_label,
        "expiry_dte": dte,
        "premium_estimate": premium,
        "contracts": contracts,
        "max_risk": max_risk,
        "profit_target": profit_target,
        "stop_loss": stop_loss,
        "time_exit_label": OPTIONS_EXIT_RULES["time_exit_day"],
        "conviction_grade": grade,
        "entry_reason": entry_reason,
        "size_label": size_label,
        "psychology_note": _psychology_note(trigger_type),
        "trigger_type": trigger_type,
        "trigger_condition": _trigger_condition_label(trigger_type, ticker, strike, drop_pct),
        "blocked": False,
        "block_reasons": [],
    }


def compute_naked_call_signal(
    ticker: str,
    current_price: float,
    vix_price: float,
    regime: str,
    trigger_type: str,
    brando_levels: list = None,
    bracket_alignment: bool = False,
) -> dict:
    """
    Compute a full naked call recommendation.
    Bearish direction — sell call above current price.
    """
    today = date.today()

    # ── Block check ─────────────────────────────────────────
    blocked, block_reasons = _is_blocked(regime, today)
    if blocked:
        return {
            "action": "NO TRADE",
            "blocked": True,
            "block_reasons": block_reasons,
        }

    # ── Brando alignment ────────────────────────────────────
    brando_hit = False
    brando_note = ""
    if brando_levels:
        for lvl in brando_levels:
            if lvl.get("ticker", "").upper() != ticker.upper():
                continue
            lvl_type = lvl.get("type", "")
            lvl_price = lvl.get("price", 0)
            if lvl_type in ("resistance", "supply") and lvl_price > 0:
                distance_pct = abs(current_price - lvl_price) / current_price
                if distance_pct < 0.03:
                    brando_hit = True
                    brando_note = f"Brando {lvl_type} at ${lvl_price:,.0f}"
                    break

    # ── Conviction grade ────────────────────────────────────
    grade = _compute_conviction_grade(
        trigger_type, regime, brando_hit, bracket_alignment,
    )
    if grade == "F":
        return {"action": "NO TRADE", "blocked": True, "block_reasons": ["Grade F"]}

    # ── Strike selection ────────────────────────────────────
    otm_price = current_price * (1 + OPTIONS_OTM_PCT)
    strike = _round_to_strike_up(otm_price, ticker)

    # For resistance zone triggers, use the resistance level if sensible
    if trigger_type == "resistance_zone" and brando_levels:
        for lvl in brando_levels:
            if (lvl.get("ticker", "").upper() == ticker.upper()
                    and lvl.get("type") in ("resistance", "supply")):
                candidate = _round_to_strike_up(lvl["price"], ticker)
                if candidate > current_price:
                    strike = candidate
                    break

    # ── Expiry ──────────────────────────────────────────────
    exp_date, dte, exp_label = _next_weekly_expiry(today)

    # ── Premium estimate ────────────────────────────────────
    iv = OPTIONS_TYPICAL_IV.get(ticker, 0.20)
    vix_scale = max(0.7, min(2.0, vix_price / 18.0))
    effective_iv = iv * vix_scale
    premium = _estimate_premium(current_price, strike, effective_iv * 100, dte, "call")

    # ── Position sizing ─────────────────────────────────────
    max_risk = _get_max_risk(grade, regime)
    contracts = 1

    # ── Exit levels ─────────────────────────────────────────
    profit_target = round(premium * 0.50, 2)
    stop_loss = round(premium * OPTIONS_EXIT_RULES["stop_loss_multiplier"], 2)

    # ── Entry reason ────────────────────────────────────────
    reason_parts = []
    if trigger_type == "resistance_zone":
        reason_parts.append(f"{ticker} at resistance/supply zone ${strike:,.0f}")
        if brando_note:
            reason_parts.append(brando_note)
    elif trigger_type == "bracket_resistance":
        reason_parts.append(f"Bracket data: 94.7% NO WR on brackets above SPX")
        reason_parts.append("Market unlikely to reach strike by expiry")
    elif trigger_type == "high_vix":
        reason_parts.append(f"VIX at {vix_price:.1f} ({regime}) — elevated premium")
        reason_parts.append("Sell rich premium, let IV crush work for you")
    else:
        reason_parts.append(f"{ticker} sell call setup at {regime} VIX")

    if brando_hit and trigger_type != "resistance_zone":
        reason_parts.append(brando_note)
    if bracket_alignment:
        reason_parts.append("Bracket data confirms resistance above")

    entry_reason = ". ".join(reason_parts)

    grade_info = OPTIONS_GRADE_SIZING.get(grade, {})
    regime_info = OPTIONS_REGIME_SIZING.get(regime, {})
    size_label = f"{grade_info.get('label', '')} | {regime_info.get('label', '')}"

    return {
        "action": "SELL CALL",
        "ticker": ticker,
        "strike": strike,
        "expiry_date": exp_date.isoformat(),
        "expiry_label": exp_label,
        "expiry_dte": dte,
        "premium_estimate": premium,
        "contracts": contracts,
        "max_risk": max_risk,
        "profit_target": profit_target,
        "stop_loss": stop_loss,
        "time_exit_label": OPTIONS_EXIT_RULES["time_exit_day"],
        "conviction_grade": grade,
        "entry_reason": entry_reason,
        "size_label": size_label,
        "psychology_note": _psychology_note(trigger_type),
        "trigger_type": trigger_type,
        "trigger_condition": _trigger_condition_label(trigger_type, ticker, strike, 0),
        "blocked": False,
        "block_reasons": [],
    }


def compute_options_exit_guidance(
    position_type: str,
    entry_premium: float,
    current_premium: float,
    days_held: int,
    day_of_week: int,
    entry_day_of_week: int,
) -> dict:
    """
    Real-time exit guidance for tracked naked positions.

    For premium sellers: profit means premium DECREASED (buy back cheaper).
    Loss means premium INCREASED (costs more to close).

    Returns:
        action: "HOLD", "TAKE PROFIT", "CUT LOSS", "TIME EXIT"
        reason: explanation
        psychology_note: embedded message
    """
    if entry_premium <= 0:
        return {
            "action": "HOLD",
            "reason": "No entry premium tracked",
            "psychology_note": PSYCH_SYSTEM_TRUST,
        }

    # Profit check: premium dropped to 50% of entry = 50% profit
    profit_threshold = entry_premium * 0.50
    if current_premium > 0 and current_premium <= profit_threshold:
        return {
            "action": "TAKE PROFIT",
            "reason": (
                f"Premium dropped from ${entry_premium:.2f} to ${current_premium:.2f} "
                f"(50%+ profit). Close the trade."
            ),
            "psychology_note": "Well done. Book the win. Move on to the next setup.",
        }

    # Loss check: premium doubled
    loss_threshold = entry_premium * OPTIONS_EXIT_RULES["stop_loss_multiplier"]
    if current_premium >= loss_threshold:
        return {
            "action": "CUT LOSS",
            "reason": (
                f"Premium rose from ${entry_premium:.2f} to ${current_premium:.2f} "
                f"(2x stop hit). Cut immediately."
            ),
            "psychology_note": PSYCH_CUT_LOSER,
        }

    # Time exit: Wednesday for positions entered Monday/Tuesday with weekly expiry
    time_exit_day = 2  # Wednesday
    if day_of_week >= time_exit_day and entry_day_of_week <= 1 and days_held >= 2:
        return {
            "action": "TIME EXIT",
            "reason": (
                f"Held {days_held} days. Wednesday time exit for weekly position. "
                "Close to lock in remaining time value."
            ),
            "psychology_note": "Discipline > greed. Close and free capital for the next trade.",
        }

    # Hold
    pnl_pct = ((entry_premium - current_premium) / entry_premium * 100) if current_premium > 0 else 0
    return {
        "action": "HOLD",
        "reason": f"Day {days_held}. P&L ~{pnl_pct:+.0f}%. Target: 50% profit.",
        "psychology_note": PSYCH_HOLD_WINNER if pnl_pct > 0 else PSYCH_SYSTEM_TRUST,
    }


def compute_daily_options_intel(
    spx_price: float,
    vix_price: float,
    regime: str,
    brando_levels: list = None,
    bracket_levels: list = None,
) -> dict:
    """
    Generate the full OPTIONS PLAYBOOK for the morning TOS intel report.

    Returns dict with:
      - naked_put_ideas: conditional put signals for key levels
      - naked_call_ideas: conditional call signals for resistance levels
      - regime_guidance: what to do today
      - risk_budget: total risk budget for the day
    """
    brando_levels = brando_levels or []

    # ── Regime guidance ─────────────────────────────────────
    regime_guidance_map = {
        "LOW": "Full green light. Sell puts on any -1% dip. Sell calls at resistance. Max sizing.",
        "LOW_MED": "Good conditions. Sell puts on dips with 80% sizing. Watch VIX for reversion signal.",
        "MEDIUM": "Elevated VIX. Reduced sizing (60%). Only A/A+ grade setups. Prefer puts on big dips.",
        "HIGH": "High VIX — premiums are rich but risk is real. Puts only with extreme caution. 40% size.",
        "CRISIS": "ALL CASH. No naked positions. Wait for VIX to drop below 25.",
    }
    regime_guidance = regime_guidance_map.get(regime, "Wait for clarity.")

    # ── Risk budget ─────────────────────────────────────────
    regime_frac = OPTIONS_REGIME_SIZING.get(regime, {}).get("risk_frac", 0)
    risk_budget = round(OPTIONS_MAX_RISK_PER_TRADE * 2 * regime_frac, -1)  # 2 trades max

    if regime == "CRISIS":
        return {
            "naked_put_ideas": [],
            "naked_call_ideas": [],
            "regime_guidance": regime_guidance,
            "risk_budget": 0,
        }

    # ── Naked Put Ideas ─────────────────────────────────────
    put_ideas = []

    # SPY put on dip
    spy_price = spx_price / 10
    spy_put = compute_naked_put_signal(
        ticker="SPY",
        current_price=spy_price,
        vix_price=vix_price,
        regime=regime,
        trigger_type="daily_intel",
        drop_pct=1.0,
        brando_levels=brando_levels,
    )
    if spy_put.get("action") == "SELL PUT":
        spy_put["trigger_condition"] = f"SPY dips to ~${spy_price * 0.99:.0f} (-1%)"
        put_ideas.append(spy_put)

    # QQQ put on dip
    qqq_est = spx_price * 0.0883
    qqq_put = compute_naked_put_signal(
        ticker="QQQ",
        current_price=qqq_est,
        vix_price=vix_price,
        regime=regime,
        trigger_type="daily_intel",
        brando_levels=brando_levels,
    )
    if qqq_put.get("action") == "SELL PUT":
        qqq_put["trigger_condition"] = f"QQQ dips to ~${qqq_est * 0.99:.0f} (-1%)"
        put_ideas.append(qqq_put)

    # Ticker-specific puts at Brando demand zones
    for lvl in brando_levels:
        lvl_ticker = lvl.get("ticker", "").upper()
        if lvl_ticker in ("SPY", "QQQ", "SPX"):
            continue  # Already covered above
        if lvl.get("type") not in ("support", "demand_zone"):
            continue
        lvl_price = lvl.get("price", 0)
        if lvl_price <= 0:
            continue

        sig = compute_naked_put_signal(
            ticker=lvl_ticker,
            current_price=lvl_price * 1.02,  # assume slightly above level
            vix_price=vix_price,
            regime=regime,
            trigger_type="daily_intel_weak",
            brando_levels=brando_levels,
        )
        if sig.get("action") == "SELL PUT":
            note = lvl.get("note", "")
            sig["trigger_condition"] = f"{lvl_ticker} drops to ${lvl_price:,.0f} ({note})"
            put_ideas.append(sig)

    # ── Naked Call Ideas ────────────────────────────────────
    call_ideas = []

    # Ticker-specific calls at Brando resistance/supply zones
    for lvl in brando_levels:
        lvl_ticker = lvl.get("ticker", "").upper()
        if lvl.get("type") not in ("resistance", "supply", "target"):
            continue
        lvl_price = lvl.get("price", 0)
        if lvl_price <= 0:
            continue

        sig = compute_naked_call_signal(
            ticker=lvl_ticker,
            current_price=lvl_price * 0.98,  # assume slightly below level
            vix_price=vix_price,
            regime=regime,
            trigger_type="daily_intel_weak",
            brando_levels=brando_levels,
        )
        if sig.get("action") == "SELL CALL":
            note = lvl.get("note", "")
            sig["trigger_condition"] = f"{lvl_ticker} rallies to ${lvl_price:,.0f} ({note})"
            call_ideas.append(sig)

    # Limit to top 3 each
    put_ideas = put_ideas[:3]
    call_ideas = call_ideas[:3]

    return {
        "naked_put_ideas": put_ideas,
        "naked_call_ideas": call_ideas,
        "regime_guidance": regime_guidance,
        "risk_budget": risk_budget,
    }


def _trigger_condition_label(trigger_type: str, ticker: str, strike: float, drop_pct: float) -> str:
    """Human-readable trigger condition for daily intel."""
    if trigger_type == "spx_dip":
        return f"SPX dips {drop_pct:.0f}%+ intraday"
    if trigger_type == "vix_reversion":
        return "VIX crosses above 20 then drops below 19"
    if trigger_type == "demand_zone":
        return f"{ticker} drops to ${strike:,.0f} demand zone"
    if trigger_type == "resistance_zone":
        return f"{ticker} rallies to ${strike:,.0f} resistance"
    if trigger_type == "daily_intel":
        return f"{ticker} dips -1% from open"
    return f"{ticker} at ${strike:,.0f}"
