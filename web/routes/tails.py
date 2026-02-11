"""
PredictorX — S&P Tail API Routes
GET /api/tails — tail selling opportunities with VIX regime.
"""

from fastapi import APIRouter

from config.settings import get_settings
from config.constants import TAIL_PROB, VIX_REGIMES
from core.registry import StrategyRegistry
from db.repository import Repository

router = APIRouter()


@router.get("/tails")
async def get_tail_analysis():
    """Get S&P tail predictions with VIX regime context."""
    settings = get_settings()
    registry = StrategyRegistry()
    predictions = await registry.scan_strategy("sp_tail", balance=settings.starting_capital)

    # VIX context
    vix_data = None
    try:
        from adapters.kalshi_data import get_vix, get_spx
        vix = get_vix()
        spx = get_spx()
        vix_data = {
            "price": vix["price"],
            "regime": vix["regime"],
            "spx_price": spx.get("price"),
            "spx_change_pct": spx.get("change_pct"),
        }
    except Exception:
        pass

    regime = vix_data["regime"] if vix_data else "UNKNOWN"

    return {
        "vix": vix_data,
        "regime_info": VIX_REGIMES.get(regime, {}),
        "historical_probs": TAIL_PROB.get(regime, {}),
        "predictions": [
            {
                "market_ticker": p.market_ticker,
                "market_title": p.market_title,
                "pct_drop": p.confidence_factors.get("pct_drop"),
                "historical_prob": p.confidence_factors.get("hist_prob", 0),
                "market_price": round(p.market_price, 4),
                "edge": round(p.edge, 4),
                "confidence": round(p.confidence_score, 4),
                "side": p.side,
                "recommended_contracts": p.recommended_contracts,
                "recommended_cost": round(p.recommended_cost, 2),
            }
            for p in predictions
        ],
    }


@router.get("/tails/history")
async def get_vix_history(hours: int = 24):
    """Get recent VIX snapshots for charting."""
    settings = get_settings()
    repo = Repository(settings.database_sync_url)

    from datetime import datetime, timedelta
    from db.models import VixSnapshotRecord

    since = datetime.utcnow() - timedelta(hours=hours)
    with repo._session() as session:
        records = session.query(VixSnapshotRecord).filter(
            VixSnapshotRecord.fetched_at >= since
        ).order_by(VixSnapshotRecord.fetched_at).all()

        return {
            "count": len(records),
            "snapshots": [
                {
                    "timestamp": r.fetched_at.isoformat(),
                    "vix": r.vix_price,
                    "regime": r.regime,
                    "spx": r.spx_price,
                }
                for r in records
            ],
        }
