"""
PredictorX — Kelly Criterion + 7 Safety Gates (Growth Mode)
Unified position sizing with dynamic scaling as balance grows.
"""

import math
import logging
from core.models import Prediction
from config.constants import (
    HARD_BALANCE_FLOOR, MAX_SINGLE_TRADE, DAILY_DEPLOYMENT_CAP,
    MAX_OPEN_POSITIONS, MIN_EDGE_PCT, KELLY_FRACTION, MIN_CONTRACTS,
    DYNAMIC_SIZING, GROWTH_TIERS, MAX_SINGLE_TRADE_PCT, DAILY_DEPLOYMENT_PCT,
)

logger = logging.getLogger(__name__)


def _get_dynamic_limits(balance: float) -> dict:
    """
    Scale position limits based on current balance.
    As balance grows, percentages tighten slightly to protect gains.
    """
    if not DYNAMIC_SIZING:
        return {
            "max_trade": MAX_SINGLE_TRADE,
            "daily_cap": DAILY_DEPLOYMENT_CAP,
            "kelly_frac": KELLY_FRACTION,
        }

    # Find the appropriate tier
    tier = None
    for threshold in sorted(GROWTH_TIERS.keys(), reverse=True):
        if balance >= threshold:
            tier = GROWTH_TIERS[threshold]
            break

    if tier is None:
        tier = GROWTH_TIERS[min(GROWTH_TIERS.keys())]

    return {
        "max_trade": max(MAX_SINGLE_TRADE, balance * tier["max_trade_pct"]),
        "daily_cap": max(DAILY_DEPLOYMENT_CAP, balance * tier["deploy_pct"]),
        "kelly_frac": tier["kelly"],
    }


def kelly_sizing(
    prediction: Prediction,
    balance: float,
    daily_deployed: float = 0.0,
    open_positions: int = 0,
) -> dict:
    """
    Compute Kelly criterion position sizing with 7 safety gates.
    Dynamically scales limits based on current balance for growth mode.

    Returns dict with:
    - kelly_fraction: raw Kelly fraction
    - recommended_contracts: number of contracts
    - recommended_cost: total cost in USD
    - passed_gates: list of passed safety checks
    - blocked_reason: reason if blocked (None if OK)
    """
    limits = _get_dynamic_limits(balance)
    max_trade = limits["max_trade"]
    daily_cap = limits["daily_cap"]
    kelly_frac = limits["kelly_frac"]

    result = {
        "kelly_fraction": 0.0,
        "recommended_contracts": 0,
        "recommended_cost": 0.0,
        "passed_gates": [],
        "blocked_reason": None,
    }

    # ── Gate 1: Balance Floor ─────────────────────────────
    if balance < HARD_BALANCE_FLOOR:
        result["blocked_reason"] = f"Balance ${balance:.2f} below floor ${HARD_BALANCE_FLOOR}"
        return result
    result["passed_gates"].append("balance_floor")

    # ── Gate 2: Max Single Trade ──────────────────────────
    # Computed after Kelly
    result["passed_gates"].append("max_single_trade")

    # ── Gate 3: Daily Deployment Cap ──────────────────────
    remaining_budget = daily_cap - daily_deployed
    if remaining_budget <= 0:
        result["blocked_reason"] = f"Daily cap reached (${daily_deployed:.2f}/${daily_cap:.2f})"
        return result
    result["passed_gates"].append("daily_cap")

    # ── Gate 4: Max Open Positions ────────────────────────
    if open_positions >= MAX_OPEN_POSITIONS:
        result["blocked_reason"] = f"Max positions reached ({open_positions}/{MAX_OPEN_POSITIONS})"
        return result
    result["passed_gates"].append("max_positions")

    # ── Gate 5: Minimum Edge ──────────────────────────────
    if abs(prediction.edge) < MIN_EDGE_PCT:
        result["blocked_reason"] = f"Edge {prediction.edge:.1%} below minimum {MIN_EDGE_PCT:.1%}"
        return result
    result["passed_gates"].append("min_edge")

    # ── Gate 6: Kelly Criterion ───────────────────────────
    # For binary markets:
    # Full Kelly: f* = (b*p - q) / b
    # where p = our win probability, q = 1-p, b = payout odds

    p = prediction.calibrated_probability or prediction.predicted_probability
    q = 1 - p
    market_price = prediction.market_price

    if market_price <= 0 or market_price >= 1:
        result["blocked_reason"] = f"Invalid market price: {market_price}"
        return result

    # Payout odds (how much we win per dollar risked)
    if prediction.side == "no":
        b = market_price / (1 - market_price)  # Buying NO
    else:
        b = (1 - market_price) / market_price   # Buying YES

    full_kelly = (b * p - q) / b if b > 0 else 0
    fractional_kelly = max(0, full_kelly * kelly_frac)

    result["kelly_fraction"] = round(fractional_kelly, 4)
    result["passed_gates"].append("kelly_criterion")

    if fractional_kelly <= 0:
        result["blocked_reason"] = "Negative Kelly — no edge"
        return result

    # ── Gate 7: Min Contracts ─────────────────────────────
    # Calculate position size
    position_size = balance * fractional_kelly
    position_size = min(position_size, max_trade)
    position_size = min(position_size, remaining_budget)

    # Calculate contracts
    cost_per_contract = (1 - market_price) if prediction.side == "no" else market_price
    if cost_per_contract <= 0:
        result["blocked_reason"] = "Invalid cost per contract"
        return result

    contracts = int(position_size / cost_per_contract)
    contracts = max(contracts, MIN_CONTRACTS)

    actual_cost = contracts * cost_per_contract

    if actual_cost > max_trade:
        contracts = int(max_trade / cost_per_contract)
        actual_cost = contracts * cost_per_contract

    result["passed_gates"].append("min_contracts")
    result["recommended_contracts"] = contracts
    result["recommended_cost"] = round(actual_cost, 2)

    # Update prediction
    prediction.kelly_fraction = fractional_kelly
    prediction.recommended_contracts = contracts
    prediction.recommended_cost = round(actual_cost, 2)

    return result
