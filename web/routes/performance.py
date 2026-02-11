"""
PredictorX — Performance API Routes
GET /api/performance — accuracy, P&L, and strategy breakdown.
"""

from fastapi import APIRouter, Query
from datetime import date, timedelta

from config.settings import get_settings
from db.repository import Repository
from db.models import DailyPerformanceRecord

router = APIRouter()


@router.get("/performance")
async def get_performance(days: int = Query(30, ge=1, le=365)):
    """Get performance summary over the last N days."""
    settings = get_settings()
    repo = Repository(settings.database_sync_url)
    return repo.get_performance_summary(days=days)


@router.get("/performance/daily")
async def get_daily_performance(days: int = Query(30, ge=1, le=365)):
    """Get daily performance history for charting."""
    settings = get_settings()
    repo = Repository(settings.database_sync_url)

    since = date.today() - timedelta(days=days)
    with repo._session() as session:
        records = session.query(DailyPerformanceRecord).filter(
            DailyPerformanceRecord.date >= since
        ).order_by(DailyPerformanceRecord.date).all()

        return {
            "count": len(records),
            "daily": [
                {
                    "date": r.date.isoformat(),
                    "total": r.total_predictions,
                    "correct": r.correct_predictions,
                    "accuracy": round(r.accuracy or 0, 4),
                    "pnl": round(r.hypothetical_pnl or 0, 2),
                    "cumulative_pnl": round(r.cumulative_pnl or 0, 2),
                    "vix": r.avg_vix,
                    "regime": r.vix_regime,
                }
                for r in records
            ],
        }


@router.get("/performance/predictions")
async def get_prediction_history(
    limit: int = Query(50, ge=1, le=200),
    strategy: str = Query(None),
):
    """Get individual prediction history."""
    settings = get_settings()
    repo = Repository(settings.database_sync_url)
    predictions = repo.get_recent_predictions(limit=limit, strategy=strategy)

    return {
        "count": len(predictions),
        "predictions": [
            {
                "id": p.id,
                "strategy": p.strategy,
                "market": p.market_title or p.market_ticker,
                "side": p.side,
                "edge": round(p.edge, 4),
                "confidence": round(p.confidence_score, 4),
                "contracts": p.recommended_contracts,
                "cost": round(p.recommended_cost or 0, 2),
                "outcome": p.outcome,
                "pnl": round(p.pnl or 0, 2) if p.pnl else None,
                "created": p.created_at.isoformat() if p.created_at else None,
                "settled": p.settled_at.isoformat() if p.settled_at else None,
            }
            for p in predictions
        ],
    }
