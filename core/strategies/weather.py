"""
PredictorX — Weather Strategy
Wraps polymarket-trader's WeatherAnalyzer + CalibrationEngine.
4-source ensemble with 7 edge enhancements vs Kalshi temperature markets.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from core.strategies.base import Strategy
from core.models import Prediction
from config.constants import KALSHI_STATIONS, CONFIDENCE_WEIGHTS

logger = logging.getLogger(__name__)


class WeatherStrategy(Strategy):

    @property
    def name(self) -> str:
        return "weather"

    @property
    def description(self) -> str:
        return "4-source ensemble weather forecasting vs Kalshi temperature markets"

    def __init__(self):
        self._analyzer = None
        self._calibration = None

    def _load_modules(self):
        """Lazy-load the weather analyzer and calibration engine."""
        if self._analyzer is None:
            from adapters.polymarket_trader import get_weather_analyzer
            self._analyzer = get_weather_analyzer()
        if self._calibration is None:
            from adapters.polymarket_trader import get_calibration_engine
            self._calibration = get_calibration_engine()

    async def is_available(self) -> bool:
        try:
            self._load_modules()
            return self._analyzer is not None
        except Exception:
            return False

    async def scan(self) -> list[Prediction]:
        """
        Scan all Kalshi weather markets and generate predictions.
        Uses the 4-source ensemble from polymarket-trader.
        """
        predictions = []

        try:
            self._load_modules()
        except Exception as e:
            logger.warning(f"Weather strategy modules not available: {e}")
            return predictions

        # For each tracked city, analyze forecasts
        for city_code, station_info in KALSHI_STATIONS.items():
            try:
                city_preds = await self._analyze_city(city_code, station_info)
                predictions.extend(city_preds)
            except Exception as e:
                logger.error(f"Error analyzing {city_code}: {e}")

        # Sort by edge (highest first)
        predictions.sort(key=lambda p: abs(p.edge), reverse=True)
        return predictions

    async def _analyze_city(self, city_code: str, station_info: dict) -> list[Prediction]:
        """Analyze weather forecasts for a single city."""
        predictions = []

        # Analyze today through 5 days out (more days = more bracket opportunities)
        today = datetime.now()
        for days_ahead in range(0, 6):
            target_date = today + timedelta(days=days_ahead)
            date_str = target_date.strftime("%Y-%m-%d")

            try:
                forecast = await self._get_ensemble_forecast(city_code, date_str)
                if forecast is None:
                    continue

                consensus = forecast.get("consensus_high")
                if consensus is None:
                    continue

                agreement = forecast.get("source_agreement", 0.5)

                # Create prediction for temperature bracket
                pred = Prediction(
                    strategy="weather",
                    market_ticker=f"KXHIGH{city_code}-{target_date.strftime('%d%b%y').upper()}",
                    market_title=f"{city_code} High Temp {date_str}",
                    platform="kalshi",
                    predicted_probability=forecast.get("predicted_prob", 0.5),
                    calibrated_probability=forecast.get("calibrated_prob", 0.5),
                    market_price=forecast.get("market_price", 0.5),
                    edge=forecast.get("edge", 0.0),
                    confidence_score=0.0,  # Set by scoring layer
                    side=forecast.get("side", "yes"),
                    expiry=target_date.replace(hour=23, minute=59),
                    confidence_factors={
                        "source_agreement": agreement,
                        "forecast_horizon": days_ahead,
                        "consensus_high": consensus,
                        "city": city_code,
                    },
                )

                # Compute confidence factors
                factors = await self.get_confidence_factors(pred)
                pred.confidence_factors.update(factors)

                if abs(pred.edge) >= 0.03:  # 3c minimum edge — aggressive mode
                    predictions.append(pred)

            except Exception as e:
                logger.debug(f"Error for {city_code} {date_str}: {e}")

        return predictions

    async def _get_ensemble_forecast(self, city_code: str, date_str: str) -> Optional[dict]:
        """
        Get ensemble forecast from the WeatherAnalyzer.
        Falls back to a simplified version if the full analyzer isn't available.
        """
        if self._analyzer and hasattr(self._analyzer, "WeatherAnalyzer"):
            try:
                analyzer = self._analyzer.WeatherAnalyzer()
                result = analyzer.analyze(city_code, date_str)
                return result
            except Exception as e:
                logger.debug(f"Full analyzer failed for {city_code}: {e}")

        # Simplified forecast using NWS API directly
        try:
            return await self._simple_nws_forecast(city_code, date_str)
        except Exception as e:
            logger.debug(f"Simple NWS forecast failed: {e}")
            return None

    async def _simple_nws_forecast(self, city_code: str, date_str: str) -> Optional[dict]:
        """Simplified NWS forecast fetch as fallback."""
        import httpx

        station = KALSHI_STATIONS.get(city_code, {})
        # NWS grid points for major cities
        grid_points = {
            "NYC": ("OKX", 33, 37),
            "CHI": ("LOT", 65, 76),
            "MIA": ("MFL", 75, 53),
            "PHI": ("PHI", 57, 97),
            "AUS": ("EWX", 154, 93),
            "DEN": ("BOU", 62, 60),
            "SFO": ("MTR", 84, 105),
        }

        gp = grid_points.get(city_code)
        if not gp:
            return None

        office, x, y = gp
        url = f"https://api.weather.gov/gridpoints/{office}/{x},{y}/forecast"

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers={
                "User-Agent": "PredictorX/1.0 (prediction-platform)",
                "Accept": "application/geo+json",
            })
            if resp.status_code != 200:
                return None

            data = resp.json()
            periods = data.get("properties", {}).get("periods", [])

            # Find the matching date's high
            for period in periods:
                if period.get("isDaytime") and date_str in period.get("startTime", ""):
                    temp = period.get("temperature")
                    return {
                        "consensus_high": temp,
                        "source_agreement": 0.6,  # Single source
                        "predicted_prob": 0.5,
                        "calibrated_prob": 0.5,
                        "market_price": 0.5,
                        "edge": 0.0,
                        "side": "yes",
                    }

        return None

    async def get_confidence_factors(self, prediction: Prediction) -> dict:
        """Weather-specific confidence factors."""
        factors = prediction.confidence_factors.copy()

        # Source agreement (0-1)
        agreement = factors.get("source_agreement", 0.5)

        # Forecast horizon decay
        horizon = factors.get("forecast_horizon", 1)
        horizon_decay = {0: 1.0, 1: 0.95, 2: 0.85, 3: 0.70, 4: 0.55, 5: 0.40}
        factors["horizon_confidence"] = horizon_decay.get(horizon, 0.40)

        # City reliability (from historical data)
        city = factors.get("city", "")
        city_reliability = {
            "SFO": 0.95, "CHI": 0.90, "DEN": 0.85,
            "NYC": 0.85, "MIA": 0.80, "AUS": 0.70, "PHI": 0.70,
        }
        factors["city_reliability"] = city_reliability.get(city, 0.75)

        # Model agreement score
        factors["model_agreement"] = agreement

        return factors
