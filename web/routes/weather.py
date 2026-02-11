"""
PredictorX — Weather API Routes
GET /api/weather — weather predictions and forecast data.
"""

from fastapi import APIRouter, Query

from config.settings import get_settings
from config.constants import KALSHI_STATIONS
from core.registry import StrategyRegistry
from db.repository import Repository

router = APIRouter()


@router.get("/weather")
async def get_weather_predictions(city: str = Query(None)):
    """Get weather predictions, optionally filtered by city."""
    settings = get_settings()
    registry = StrategyRegistry()
    predictions = await registry.scan_strategy("weather", balance=settings.starting_capital)

    if city:
        city_upper = city.strip().upper()
        predictions = [p for p in predictions if p.confidence_factors.get("city") == city_upper]

    return {
        "count": len(predictions),
        "cities": list(KALSHI_STATIONS.keys()),
        "predictions": [
            {
                "market_ticker": p.market_ticker,
                "market_title": p.market_title,
                "city": p.confidence_factors.get("city", "?"),
                "consensus_high": p.confidence_factors.get("consensus_high"),
                "source_agreement": round(p.confidence_factors.get("source_agreement", 0), 4),
                "edge": round(p.edge, 4),
                "confidence": round(p.confidence_score, 4),
                "side": p.side,
                "recommended_contracts": p.recommended_contracts,
                "recommended_cost": round(p.recommended_cost, 2),
                "forecast_horizon": p.confidence_factors.get("forecast_horizon", 0),
            }
            for p in predictions
        ],
    }


@router.get("/weather/forecasts")
async def get_weather_forecasts(city: str = Query(None), days: int = Query(7, ge=1, le=30)):
    """Get raw weather forecast data from database."""
    settings = get_settings()
    repo = Repository(settings.database_sync_url)

    from datetime import date, timedelta
    from db.models import WeatherForecastRecord

    with repo._session() as session:
        since = date.today() - timedelta(days=days)
        q = session.query(WeatherForecastRecord).filter(
            WeatherForecastRecord.forecast_date >= since
        )
        if city:
            q = q.filter(WeatherForecastRecord.city == city.upper())
        records = q.order_by(WeatherForecastRecord.forecast_date.desc()).limit(100).all()

        return {
            "count": len(records),
            "forecasts": [
                {
                    "city": r.city,
                    "date": r.forecast_date.isoformat(),
                    "nws_high": r.nws_high,
                    "open_meteo_high": r.open_meteo_high,
                    "weatherapi_high": r.weatherapi_high,
                    "visualcrossing_high": r.visualcrossing_high,
                    "consensus_high": r.consensus_high,
                    "source_agreement": r.source_agreement,
                }
                for r in records
            ],
        }
