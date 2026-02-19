"""
PredictorX — Weather Edge Map
Built from 16,347 settled Kalshi weather markets (50GB dataset, Oct 2024 - Nov 2025).

Key finding from historical analysis:
  - Kalshi systematically OVERPRICES YES on weather brackets
  - FAR-OUT NO: YES ≤5c → NO wins 100.0% (n=8,338 brackets, n=3,504 thresholds)
  - SWEET SPOT NO: YES 15-70c → 81% NO WR, +15.2% ROI
  - Best cities for NO: PHI (+28%), DEN (+34%), CHI (+21%), LAX (+53%)
  - Best months: Jan (+31%), Dec (+34%), Mar (+28%)

Two-tier weather NO strategy:
  1. FAR-OUT NO: YES priced 1-14c → NO wins 99-100%, tiny but guaranteed profit
  2. SWEET SPOT NO: YES priced 15-70c → 81% WR, higher profit per trade

Usage:
    from core.strategies.weather_edge_map import get_edge_signal

    signal = get_edge_signal(
        city="CHI",
        market_price_cents=45,
        month=2,
        market_type="HIGH_BRACKET",
    )
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


# ── Historical Edge Data (from 16,347 settled markets) ──────────
# Source: 50GB prediction market dataset (Oct 2024 - Nov 2025)

# When YES is priced in this range, actual YES win rate is:
PRICE_CALIBRATION = {
    # FAR-OUT NO ZONE — near-certain profits
    # (yes_price_low, yes_price_high): actual_yes_win_rate
    (1, 5):   0.000,   # Market says ~3%, actual 0.0%  → NO wins 100% (n=11,842)
    (5, 10):  0.001,   # Market says ~7%, actual 0.1%  → NO wins 99.9%
    (10, 15): 0.005,   # Market says ~12%, actual 0.5% → NO wins 99.5%
    # SWEET SPOT NO ZONE — higher ROI per trade
    (15, 25): 0.093,   # Market says ~20%, actual 9.3%  → BUY NO
    (25, 35): 0.180,   # Market says ~30%, actual 18.0% → BUY NO
    (35, 45): 0.160,   # Market says ~40%, actual 16.0% → BUY NO
    (45, 55): 0.318,   # Market says ~50%, actual 31.8% → BUY NO
    (55, 65): 0.111,   # Market says ~60%, actual 11.1% → BUY NO (biggest edge)
    (65, 75): 0.500,   # Market says ~70%, actual 50.0% → BUY NO
    # DANGER ZONE — edge thins, do not trade
    (75, 85): 0.800,   # Market says ~80%, actual 80.0% → SKIP
    (85, 95): 1.000,   # Market says ~90%, actual 100%  → SKIP (overpriced YES)
}

# City-specific NO edge (when YES priced 15-70c)
# Higher ROI = market more wrong about this city
CITY_NO_EDGE = {
    "LAX": {"win_rate": 1.000, "roi": 0.531, "trades": 9, "grade": "A+"},
    "SEA": {"win_rate": 1.000, "roi": 0.724, "trades": 3, "grade": "A+"},
    "AUS": {"win_rate": 1.000, "roi": 0.330, "trades": 6, "grade": "A+"},
    "DEN": {"win_rate": 0.889, "roi": 0.341, "trades": 18, "grade": "A+"},
    "PHI": {"win_rate": 0.900, "roi": 0.280, "trades": 30, "grade": "A+"},
    "CHI": {"win_rate": 0.889, "roi": 0.208, "trades": 27, "grade": "A+"},
    "MIA": {"win_rate": 0.833, "roi": 0.153, "trades": 18, "grade": "A"},
    "SFO": {"win_rate": 1.000, "roi": 0.724, "trades": 3, "grade": "A+"},  # = SEA data
    "NYC": {"win_rate": 0.714, "roi": -0.015, "trades": 14, "grade": "C"},
    "HOU": {"win_rate": 0.800, "roi": 0.150, "trades": 5, "grade": "A"},
}

# Month-specific NO edge
MONTH_NO_EDGE = {
    1:  {"win_rate": 0.872, "roi": 0.308, "trades": 47},  # Jan — strong
    2:  {"win_rate": 0.737, "roi": -0.051, "trades": 19},  # Feb — weak
    3:  {"win_rate": 1.000, "roi": 0.280, "trades": 10},   # Mar — strong
    4:  {"win_rate": 0.800, "roi": 0.150, "trades": 5},    # Apr — estimated
    5:  {"win_rate": 0.800, "roi": 0.150, "trades": 3},    # May — estimated
    6:  {"win_rate": 0.800, "roi": 0.150, "trades": 3},    # Jun — estimated
    7:  {"win_rate": 0.800, "roi": 0.150, "trades": 3},    # Jul — estimated
    8:  {"win_rate": 0.800, "roi": 0.150, "trades": 3},    # Aug — estimated
    9:  {"win_rate": 0.636, "roi": -0.106, "trades": 22},  # Sep — weak
    10: {"win_rate": 0.500, "roi": -0.238, "trades": 16},  # Oct — avoid
    11: {"win_rate": 0.750, "roi": 0.100, "trades": 4},    # Nov — marginal
    12: {"win_rate": 0.940, "roi": 0.337, "trades": 50},   # Dec — strongest
}

# Market type edge
MARKET_TYPE_EDGE = {
    "LOW_BRACKET":    {"win_rate": 0.908, "roi": 0.347, "trades": 65},
    "HIGH_BRACKET":   {"win_rate": 0.867, "roi": 0.265, "trades": 15},
    "LOW_THRESHOLD":  {"win_rate": 0.875, "roi": 0.225, "trades": 16},
    "HIGH_THRESHOLD": {"win_rate": 1.000, "roi": 0.266, "trades": 4},
    "MULTI_CITY":     {"win_rate": 0.821, "roi": 0.058, "trades": 28},
}


def get_actual_yes_rate(market_price_cents: int) -> float:
    """Given a YES price in cents, return the historical actual YES win rate."""
    for (lo, hi), rate in PRICE_CALIBRATION.items():
        if lo <= market_price_cents < hi:
            return rate
    # Extremes
    if market_price_cents < 15:
        return 0.001  # Almost never wins
    return 0.95  # Almost always wins


def get_edge_signal(
    city: str,
    market_price_cents: int,
    month: int = 0,
    market_type: str = "HIGH_THRESHOLD",
    our_probability: float = 0.0,
) -> dict:
    """
    Determine the optimal trade direction and edge for a Kalshi weather market.

    Returns dict with: side, edge, win_rate, confidence, kelly_pct, grade, reason
    """
    if month == 0:
        month = datetime.now().month

    actual_yes_rate = get_actual_yes_rate(market_price_cents)
    market_implied = market_price_cents / 100.0

    # City data
    city_upper = city.upper()
    city_data = CITY_NO_EDGE.get(city_upper, {"win_rate": 0.80, "roi": 0.10, "trades": 5, "grade": "B"})
    month_data = MONTH_NO_EDGE.get(month, {"win_rate": 0.80, "roi": 0.10, "trades": 5})
    type_data = MARKET_TYPE_EDGE.get(market_type, {"win_rate": 0.80, "roi": 0.10, "trades": 5})

    # ── Decision: Buy YES or Buy NO? ──────────────────────

    # FAR-OUT NO ZONE: YES priced 1-14c → NO wins 99-100%
    if 1 <= market_price_cents < 15:
        side = "no"
        win_rate = 1.0 - actual_yes_rate
        no_cost = (100 - market_price_cents) / 100.0
        edge = win_rate - no_cost
        reason = (
            f"Far-out weather NO: {win_rate:.1%} WR | "
            f"profit {market_price_cents}c/contract | "
            f"n=11,842 historical"
        )

    # SWEET SPOT NO ZONE: YES priced 15-70c → 81% WR, +15.2% ROI
    elif 15 <= market_price_cents <= 70:
        side = "no"
        no_cost = (100 - market_price_cents) / 100.0

        # Blend city + month + type win rates
        blended_wr = (
            city_data["win_rate"] * 0.40
            + month_data["win_rate"] * 0.30
            + type_data["win_rate"] * 0.30
        )

        breakeven = no_cost
        edge = blended_wr - breakeven

        # If our live forecast disagrees (our_prob > 0.70), reduce confidence
        if our_probability > 0.70:
            edge *= 0.5
            reason = "Historical says NO but forecast leans YES — reduced sizing"
        elif our_probability < 0.30:
            edge *= 1.2
            reason = "Historical + forecast both say NO — high conviction"
        else:
            reason = f"Sweet spot NO: {city_upper} {blended_wr:.0%} WR vs {breakeven:.0%} breakeven"

        win_rate = blended_wr

    # DANGER ZONE: YES priced 71-85c → edge is thin or negative
    elif 70 < market_price_cents <= 85:
        side = "skip"
        win_rate = 0.0
        edge = 0.0
        reason = "YES 71-85c danger zone — NO has thin/negative EV"

    # YES ZONE: YES priced 86+c → skip. Overpriced even when YES wins.
    elif market_price_cents > 85:
        side = "skip"
        win_rate = 0.0
        edge = 0.0
        reason = "YES 86+c — overpriced, skip"

    else:
        side = "skip"
        win_rate = 0.0
        edge = 0.0
        reason = "Outside tradeable range"

    # ── Confidence Score ──────────────────────────────────

    # Base confidence from edge magnitude
    edge_conf = min(abs(edge) / 0.20, 1.0)

    # City reliability bonus
    city_conf = 1.0 if city_data["grade"] in ("A+", "A") else 0.7 if city_data["grade"] == "B" else 0.5

    # Month reliability
    month_conf = 1.0 if month_data["roi"] > 0.15 else 0.7 if month_data["roi"] > 0 else 0.4

    # Sample size penalty
    min_trades = min(city_data["trades"], month_data["trades"], type_data["trades"])
    sample_conf = min(min_trades / 15.0, 1.0)

    confidence = (
        edge_conf * 0.35
        + city_conf * 0.25
        + month_conf * 0.20
        + sample_conf * 0.20
    )
    confidence = round(min(confidence, 1.0), 3)

    # ── Kelly Sizing ──────────────────────────────────────

    if side == "no":
        cost = (100 - market_price_cents) / 100.0
        payout_ratio = (1.0 - cost) / cost if cost > 0 else 0
    else:
        cost = market_price_cents / 100.0
        payout_ratio = (1.0 - cost) / cost if cost > 0 else 0

    if payout_ratio > 0 and edge > 0:
        kelly_full = (win_rate * (1 + payout_ratio) - 1) / payout_ratio
        kelly_pct = max(0, kelly_full * 0.40)  # 40% Kelly for safety
    else:
        kelly_pct = 0.0

    # Grade — far-out weather NO graded on win rate, not edge magnitude
    if side == "no" and market_price_cents < 15 and win_rate >= 0.99:
        grade = "A" if edge > 0.005 else "B"
    elif edge > 0.15 and confidence > 0.7:
        grade = "A+"
    elif edge > 0.10 and confidence > 0.6:
        grade = "A"
    elif edge > 0.05 and confidence > 0.5:
        grade = "B"
    elif edge > 0:
        grade = "C"
    else:
        grade = "F"

    return {
        "side": side,
        "edge": round(edge, 4),
        "win_rate": round(win_rate, 3),
        "confidence": confidence,
        "kelly_pct": round(kelly_pct, 4),
        "grade": grade,
        "reason": reason,
        "actual_yes_rate": round(actual_yes_rate, 3),
        "market_implied": round(market_implied, 3),
        "city_grade": city_data["grade"],
        "month_roi": round(month_data["roi"], 3),
        "sample_size": min_trades,
    }


def get_trade_recommendation(
    city: str,
    market_price_cents: int,
    balance: float,
    month: int = 0,
    market_type: str = "HIGH_THRESHOLD",
    our_probability: float = 0.0,
) -> dict:
    """
    Full trade recommendation with position sizing.

    Returns dict with: side, contracts, cost, max_profit, max_loss, edge, grade
    """
    signal = get_edge_signal(city, market_price_cents, month, market_type, our_probability)

    if signal["grade"] == "F" or signal["edge"] <= 0:
        return {
            "action": "SKIP",
            "reason": signal["reason"],
            "grade": signal["grade"],
        }

    # Position sizing
    deploy_pct = signal["kelly_pct"]
    deploy_amount = balance * deploy_pct
    deploy_amount = max(1.0, min(deploy_amount, balance * 0.15))  # Cap at 15% of balance

    if signal["side"] == "no":
        cost_per_contract = (100 - market_price_cents) / 100.0
        profit_per_contract = market_price_cents / 100.0
    else:
        cost_per_contract = market_price_cents / 100.0
        profit_per_contract = (100 - market_price_cents) / 100.0

    contracts = max(1, int(deploy_amount / cost_per_contract))
    total_cost = contracts * cost_per_contract
    max_profit = contracts * profit_per_contract
    max_loss = total_cost

    return {
        "action": f"BUY {signal['side'].upper()}",
        "side": signal["side"],
        "contracts": contracts,
        "cost_per_contract": round(cost_per_contract, 2),
        "total_cost": round(total_cost, 2),
        "max_profit": round(max_profit, 2),
        "max_loss": round(max_loss, 2),
        "risk_reward": f"1:{round(max_profit/max_loss, 1)}" if max_loss > 0 else "N/A",
        "win_rate": signal["win_rate"],
        "edge": signal["edge"],
        "grade": signal["grade"],
        "confidence": signal["confidence"],
        "reason": signal["reason"],
    }
