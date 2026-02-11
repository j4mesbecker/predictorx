"""
PredictorX — Dashboard API Route
GET /api/dashboard — overview data for the main dashboard.
"""

from datetime import datetime

from fastapi import APIRouter

from config.settings import get_settings
from db.repository import Repository

router = APIRouter()


@router.get("/dashboard")
async def get_dashboard():
    """Main dashboard data: summary stats, recent activity, system status."""
    settings = get_settings()
    repo = Repository(settings.database_sync_url)

    perf = repo.get_performance_summary(days=30)
    latest_vix = repo.get_latest_vix()
    recent_whales = repo.get_recent_whale_signals(hours=24, min_amount=1000)
    recent_predictions = repo.get_recent_predictions(limit=10)

    return {
        "timestamp": datetime.utcnow().isoformat(),
        "performance": {
            "total_predictions": perf.get("total_predictions", 0),
            "accuracy": round(perf.get("accuracy", 0), 4),
            "total_pnl": perf.get("total_pnl", 0),
            "by_strategy": perf.get("by_strategy", {}),
        },
        "vix": {
            "price": latest_vix.vix_price if latest_vix else None,
            "regime": latest_vix.regime if latest_vix else "UNKNOWN",
            "spx_price": latest_vix.spx_price if latest_vix else None,
            "updated": latest_vix.fetched_at.isoformat() if latest_vix else None,
        },
        "whale_activity": len(recent_whales),
        "recent_predictions": [
            {
                "id": p.id,
                "strategy": p.strategy,
                "market": p.market_title or p.market_ticker,
                "edge": round(p.edge, 4),
                "confidence": round(p.confidence_score, 4),
                "side": p.side,
                "outcome": p.outcome,
                "created": p.created_at.isoformat() if p.created_at else None,
            }
            for p in recent_predictions
        ],
    }
