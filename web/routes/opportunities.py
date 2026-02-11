"""
PredictorX — Opportunities API Route
GET /api/opportunities — live scanning for current opportunities.
"""

from fastapi import APIRouter, Query

from config.settings import get_settings
from core.registry import StrategyRegistry
from core.models import VixSnapshot

router = APIRouter()


@router.get("/opportunities")
async def get_opportunities(limit: int = Query(10, ge=1, le=50)):
    """Scan all strategies and return ranked opportunities."""
    settings = get_settings()
    registry = StrategyRegistry()
    opportunities = await registry.scan_all(balance=settings.starting_capital)

    # Get VIX context
    vix_data = None
    try:
        from adapters.kalshi_data import get_vix, get_spx
        vix = get_vix()
        spx = get_spx()
        vix_data = {"price": vix["price"], "regime": vix["regime"], "spx": spx.get("price")}
    except Exception:
        pass

    return {
        "count": len(opportunities),
        "vix": vix_data,
        "opportunities": [
            {
                "rank": opp.rank,
                "strategy": opp.prediction.strategy,
                "market_ticker": opp.prediction.market_ticker,
                "market_title": opp.prediction.market_title,
                "side": opp.prediction.side,
                "edge": round(opp.prediction.edge, 4),
                "confidence": round(opp.prediction.confidence_score, 4),
                "urgency": opp.urgency,
                "recommended_contracts": opp.prediction.recommended_contracts,
                "recommended_cost": round(opp.prediction.recommended_cost, 2),
                "reasons": opp.reasons,
                "vix_regime": opp.prediction.vix_regime,
            }
            for opp in opportunities[:limit]
        ],
    }
