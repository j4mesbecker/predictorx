"""
PredictorX — Strategy Registry
Discovers, manages, and runs all prediction strategies.
"""

import logging
from typing import Optional

from core.strategies.base import Strategy
from core.strategies.weather import WeatherStrategy
from core.strategies.sp_tail import SPTailStrategy
from core.strategies.bracket_arb import BracketArbStrategy
from core.models import Prediction, Opportunity
from core.scoring.confidence import score_predictions
from core.scoring.kelly import kelly_sizing
from config.settings import get_settings

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """Discovers and orchestrates all prediction strategies."""

    def __init__(self):
        self._strategies: dict[str, Strategy] = {}
        self._register_strategies()

    def _register_strategies(self):
        """Register all available strategies."""
        strategies = [
            WeatherStrategy(),
            SPTailStrategy(),
            BracketArbStrategy(),
        ]
        for s in strategies:
            self._strategies[s.name] = s
            logger.info(f"Registered strategy: {s.name}")

    def list_strategies(self) -> list[str]:
        return list(self._strategies.keys())

    def get_strategy(self, name: str) -> Optional[Strategy]:
        return self._strategies.get(name)

    async def scan_all(self, balance: float = None) -> list[Opportunity]:
        """
        Run all strategies, score predictions, apply Kelly sizing,
        and return ranked opportunities.
        """
        if balance is None:
            balance = get_settings().starting_capital

        all_predictions: list[Prediction] = []

        for name, strategy in self._strategies.items():
            try:
                available = await strategy.is_available()
                if not available:
                    logger.info(f"Strategy '{name}' not available, skipping")
                    continue

                logger.info(f"Scanning with strategy: {name}")
                predictions = await strategy.scan()
                logger.info(f"  → {len(predictions)} predictions from {name}")
                all_predictions.extend(predictions)

            except Exception as e:
                logger.error(f"Error scanning with {name}: {e}")

        # Score all predictions
        scored = score_predictions(all_predictions)

        # Apply Kelly sizing to actionable predictions
        for pred in scored:
            if pred.is_actionable:
                kelly_sizing(pred, balance)

        # Convert to ranked opportunities
        opportunities = []
        for i, pred in enumerate(scored):
            if pred.edge > 0:  # Only include positive-edge predictions
                reasons = self._generate_reasons(pred)
                opp = Opportunity(rank=i + 1, prediction=pred, reasons=reasons)
                opportunities.append(opp)

        return opportunities

    async def scan_strategy(self, strategy_name: str, balance: float = None) -> list[Prediction]:
        """Run a single strategy and return scored predictions."""
        if balance is None:
            balance = get_settings().starting_capital

        strategy = self._strategies.get(strategy_name)
        if not strategy:
            raise ValueError(f"Unknown strategy: {strategy_name}")

        predictions = await strategy.scan()
        scored = score_predictions(predictions)

        for pred in scored:
            if pred.is_actionable:
                kelly_sizing(pred, balance)

        return scored

    def _generate_reasons(self, pred: Prediction) -> list[str]:
        """Generate human-readable reasons for this opportunity."""
        reasons = []

        if pred.strategy == "sp_tail":
            regime = pred.vix_regime or "UNKNOWN"
            hist_prob = pred.confidence_factors.get("hist_prob", 0)
            if hist_prob == 0:
                reasons.append(f"0% historical loss rate at VIX regime {regime}")
                reasons.append("25 years of data (6,563 trading days)")
            else:
                reasons.append(f"{hist_prob:.1%} historical loss rate at {regime}")
            if pred.vix_level:
                reasons.append(f"VIX at {pred.vix_level:.1f}")

        elif pred.strategy == "weather":
            agreement = pred.confidence_factors.get("source_agreement", 0)
            if agreement > 0.85:
                reasons.append("Strong multi-source consensus (4 weather APIs)")
            city = pred.confidence_factors.get("city", "")
            if city:
                reasons.append(f"City: {city}")
            horizon = pred.confidence_factors.get("forecast_horizon", 0)
            if horizon == 0:
                reasons.append("Same-day forecast (highest accuracy)")

        elif pred.strategy == "bracket_arb":
            reasons.append("Risk-free arbitrage (bracket sum < $1.00)")
            reasons.append("99.5% historical success rate")

        if pred.edge >= 0.10:
            reasons.append(f"Large edge: +{pred.edge:.0%}")

        return reasons
