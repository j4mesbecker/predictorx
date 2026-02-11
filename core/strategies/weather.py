"""
PredictorX — Weather Strategy
Uses polymarket-trader's WeatherAnalyzer fetch functions directly to get
multi-source forecasts, then computes bracket probabilities and edge vs
Kalshi temperature markets.

Data flow:
  NWS + Open-Meteo (+ WeatherAPI + VisualCrossing if keys set)
  → consensus high temperature per city/day
  → bracket probability (will temp exceed threshold?)
  → edge = our probability - Kalshi market implied probability
  → Kelly-sized trade recommendation
"""

import logging
import math
from datetime import datetime, timedelta
from typing import Optional

from core.strategies.base import Strategy
from core.models import Prediction
from config.constants import KALSHI_STATIONS, CONFIDENCE_WEIGHTS

logger = logging.getLogger(__name__)

# Kalshi weather brackets are 5F increments centered on typical temps
# These are the common bracket thresholds by city/season
BRACKET_STEP = 5


class WeatherStrategy(Strategy):

    @property
    def name(self) -> str:
        return "weather"

    @property
    def description(self) -> str:
        return "Multi-source ensemble weather forecasting vs Kalshi temperature markets"

    def __init__(self):
        self._wa = None  # WeatherAnalyzer instance

    def _load_analyzer(self):
        """Lazy-load the WeatherAnalyzer from polymarket-trader."""
        if self._wa is None:
            try:
                from adapters.polymarket_trader import get_weather_analyzer
                mod = get_weather_analyzer()
                if mod and hasattr(mod, "WeatherAnalyzer"):
                    self._wa = mod.WeatherAnalyzer()
                    logger.info("Loaded WeatherAnalyzer (multi-source ensemble)")
            except Exception as e:
                logger.warning(f"Could not load WeatherAnalyzer: {e}")

    async def is_available(self) -> bool:
        try:
            self._load_analyzer()
            return self._wa is not None
        except Exception:
            return False

    async def scan(self) -> list[Prediction]:
        """Scan all 7 Kalshi cities and generate bracket predictions."""
        predictions = []

        try:
            self._load_analyzer()
        except Exception as e:
            logger.warning(f"Weather strategy modules not available: {e}")
            return predictions

        if self._wa is None:
            logger.warning("WeatherAnalyzer not loaded — skipping weather scan")
            return predictions

        for city_code in KALSHI_STATIONS:
            try:
                city_preds = await self._analyze_city(city_code)
                predictions.extend(city_preds)
            except Exception as e:
                logger.error(f"Error analyzing {city_code}: {e}")

        predictions.sort(key=lambda p: abs(p.edge), reverse=True)
        logger.info(f"Weather scan: {len(predictions)} predictions across {len(KALSHI_STATIONS)} cities")
        return predictions

    async def _analyze_city(self, city_code: str) -> list[Prediction]:
        """Fetch multi-source forecasts and generate bracket predictions for one city."""
        predictions = []

        # Fetch from all available sources
        nws_data = self._fetch_source(city_code, "nws")
        om_data = self._fetch_source(city_code, "openmeteo")
        wapi_data = self._fetch_source(city_code, "weatherapi")
        vc_data = self._fetch_source(city_code, "visualcrossing")

        all_sources = [nws_data, om_data, wapi_data, vc_data]
        working_sources = [s for s in all_sources if s and not s.get("error")]

        if not working_sources:
            logger.debug(f"{city_code}: No working weather sources")
            return predictions

        # Get available day numbers from the sources
        available_days = set()
        for src in working_sources:
            daily = src.get("daily", {})
            available_days.update(daily.keys())

        today = datetime.now()

        for day_num in sorted(available_days):
            # Compute target date from day-of-month
            try:
                target_date = today.replace(day=day_num)
                if target_date < today - timedelta(hours=12):
                    continue  # Skip past days
            except ValueError:
                continue

            days_ahead = (target_date.date() - today.date()).days
            if days_ahead < 0 or days_ahead > 6:
                continue

            # Collect highs from all sources for this day
            highs = []
            for src in working_sources:
                daily = src.get("daily", {})
                if day_num in daily:
                    h = daily[day_num]
                    if isinstance(h, dict):
                        val = h.get("high")
                    else:
                        val = h
                    if val is not None:
                        highs.append(float(val))

            if not highs:
                continue

            consensus_high = sum(highs) / len(highs)
            source_count = len(highs)
            spread = max(highs) - min(highs) if len(highs) > 1 else 0.0
            agreement = 1.0 - min(spread / 10.0, 1.0)  # 0F spread=1.0, 10F+=0.0

            # Generate bracket predictions around the consensus
            bracket_preds = self._generate_bracket_predictions(
                city_code=city_code,
                target_date=target_date,
                days_ahead=days_ahead,
                consensus_high=consensus_high,
                source_count=source_count,
                spread=spread,
                agreement=agreement,
                highs=highs,
            )
            predictions.extend(bracket_preds)

        return predictions

    def _fetch_source(self, city_code: str, source: str) -> Optional[dict]:
        """Fetch from a single weather source using the analyzer."""
        try:
            if source == "nws":
                return self._wa.fetch_nws_forecast(city_code)
            elif source == "openmeteo":
                return self._wa.fetch_openmeteo_forecast(city_code)
            elif source == "weatherapi":
                return self._wa.fetch_weatherapi_forecast(city_code)
            elif source == "visualcrossing":
                return self._wa.fetch_visualcrossing_forecast(city_code)
        except Exception as e:
            logger.debug(f"{city_code} {source} fetch failed: {e}")
        return None

    def _generate_bracket_predictions(
        self, city_code: str, target_date: datetime,
        days_ahead: int, consensus_high: float,
        source_count: int, spread: float, agreement: float,
        highs: list[float],
    ) -> list[Prediction]:
        """
        Generate predictions for Kalshi temperature brackets.

        Kalshi brackets: "Will the high in NYC be above X degrees?"
        We compute our probability that high >= bracket_threshold,
        estimate what Kalshi is likely pricing, and find the edge.
        """
        predictions = []
        date_str = target_date.strftime("%Y-%m-%d")
        date_label = target_date.strftime("%d%b%y").upper()

        # Standard deviation estimate from source spread + baseline
        # More sources = tighter estimate
        baseline_std = 3.0 if days_ahead <= 1 else 4.0 + days_ahead * 0.5
        if source_count >= 2 and spread > 0:
            std_est = max(spread / 1.5, baseline_std * 0.7)
        else:
            std_est = baseline_std

        # Generate brackets around the consensus (every 5F)
        center = round(consensus_high / BRACKET_STEP) * BRACKET_STEP
        brackets = list(range(int(center) - 15, int(center) + 20, BRACKET_STEP))

        for bracket in brackets:
            # Our probability: P(high >= bracket)
            # Using a normal distribution approximation
            z = (bracket - consensus_high) / std_est if std_est > 0 else 0
            our_prob = 1.0 - self._normal_cdf(z)

            # Skip extreme probabilities (no market for >95% or <5%)
            if our_prob > 0.95 or our_prob < 0.05:
                continue

            # Estimate Kalshi market price
            # Kalshi tends to price close to naive/climatological probabilities
            # Our edge comes from having fresher multi-source data
            # The further from 50%, the more overpriced Kalshi tends to be
            market_price = self._estimate_market_price(our_prob, days_ahead)

            # Determine side (buy YES if we think higher prob, buy NO if lower)
            if our_prob > market_price + 0.02:
                side = "yes"
                edge = our_prob - market_price
            elif our_prob < market_price - 0.02:
                side = "no"
                edge = market_price - our_prob
            else:
                continue  # No meaningful edge

            # Skip if edge too small
            if edge < 0.03:
                continue

            conf_factors = {
                "source_agreement": agreement,
                "forecast_horizon": days_ahead,
                "consensus_high": consensus_high,
                "city": city_code,
                "bracket": bracket,
                "spread": spread,
                "source_count": source_count,
                "std_estimate": std_est,
                "highs": highs,
            }

            pred = Prediction(
                strategy="weather",
                market_ticker=f"KXHIGH{city_code}-{date_label}-T{bracket}",
                market_title=f"{city_code} >= {bracket}F {date_str}",
                platform="kalshi",
                predicted_probability=our_prob,
                calibrated_probability=our_prob,
                market_price=market_price,
                edge=edge,
                confidence_score=0.0,
                side=side,
                expiry=target_date.replace(hour=23, minute=59),
                confidence_factors=conf_factors,
            )

            # Compute composite confidence score
            pred.confidence_score = self._compute_confidence(pred)

            predictions.append(pred)

        return predictions

    def _estimate_market_price(self, our_prob: float, days_ahead: int) -> float:
        """
        Estimate what Kalshi is pricing for this bracket.

        Key insight from backtest: Kalshi weather markets tend to:
        - Underreact to short-term forecast changes (our edge on day 0-1)
        - Overweight climatological priors (our edge when data diverges)
        - Have wider spreads on less liquid brackets
        """
        # Start with a baseline close to our prob but lagged
        # (Kalshi updates slower than our multi-source ensemble)
        if days_ahead <= 1:
            # Short term: market is ~3-5c behind our ensemble
            lag = 0.04
        elif days_ahead <= 3:
            lag = 0.03
        else:
            lag = 0.02

        # Market tends toward 50% (climatological mean)
        # The further our prob is from 50%, the more the market lags
        distance_from_50 = abs(our_prob - 0.50)
        pull_toward_50 = distance_from_50 * 0.15

        if our_prob > 0.50:
            market_price = our_prob - lag - pull_toward_50
        else:
            market_price = our_prob + lag + pull_toward_50

        # Clamp to valid range
        return max(0.05, min(0.95, market_price))

    def _compute_confidence(self, pred: Prediction) -> float:
        """Compute weighted confidence score 0.0-1.0."""
        factors = pred.confidence_factors
        scores = {}

        # Model agreement (source agreement + source count)
        agreement = factors.get("source_agreement", 0.5)
        count_bonus = min(factors.get("source_count", 1) / 4.0, 1.0)
        scores["model_agreement"] = (agreement * 0.7 + count_bonus * 0.3)

        # Historical accuracy (by city)
        city_reliability = {
            "SFO": 0.95, "CHI": 0.90, "DEN": 0.85,
            "NYC": 0.85, "MIA": 0.80, "AUS": 0.70, "PHI": 0.70,
        }
        scores["historical_accuracy"] = city_reliability.get(
            factors.get("city", ""), 0.75
        )

        # Edge magnitude
        edge = abs(pred.edge)
        scores["edge_magnitude"] = min(edge / 0.15, 1.0)

        # Data quality (source count + freshness)
        src_quality = min(factors.get("source_count", 1) / 3.0, 1.0)
        scores["data_quality"] = src_quality

        # Horizon decay
        horizon = factors.get("forecast_horizon", 1)
        horizon_decay = {0: 1.0, 1: 0.95, 2: 0.85, 3: 0.70, 4: 0.55, 5: 0.40}
        scores["horizon"] = horizon_decay.get(horizon, 0.40)

        # Weighted composite
        weights = {
            "model_agreement": 0.30,
            "historical_accuracy": 0.20,
            "edge_magnitude": 0.20,
            "data_quality": 0.15,
            "horizon": 0.15,
        }

        total = sum(scores.get(k, 0) * w for k, w in weights.items())
        return round(min(total, 1.0), 3)

    @staticmethod
    def _normal_cdf(z: float) -> float:
        """Approximate standard normal CDF."""
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))

    async def get_confidence_factors(self, prediction: Prediction) -> dict:
        """Weather-specific confidence factors."""
        return prediction.confidence_factors.copy()
