"""
PredictorX — Calibration API Routes
GET /api/calibration — model calibration data and metrics.
"""

from fastapi import APIRouter

from config.settings import get_settings
from db.repository import Repository
from db.models import CalibrationSnapshotRecord

router = APIRouter()


@router.get("/calibration")
async def get_calibration():
    """Get latest calibration metrics and curve data."""
    try:
        from core.scoring.calibration import get_calibration_metrics
        metrics = get_calibration_metrics()

        return {
            "total_markets": metrics.get("total_markets", 0),
            "has_full_data": metrics.get("has_full_data", False),
            "ece": metrics.get("ece"),
            "brier_score": metrics.get("brier_score"),
            "city_bias": metrics.get("city_bias", {}),
            "curve": metrics.get("calibration_curve", {}),
        }
    except Exception as e:
        return {"error": str(e), "total_markets": 0}


@router.get("/calibration/history")
async def get_calibration_history():
    """Get calibration snapshot history for tracking drift."""
    settings = get_settings()
    repo = Repository(settings.database_sync_url)

    with repo._session() as session:
        records = session.query(CalibrationSnapshotRecord).order_by(
            CalibrationSnapshotRecord.computed_at.desc()
        ).limit(20).all()

        return {
            "count": len(records),
            "snapshots": [
                {
                    "strategy": r.strategy,
                    "total_markets": r.total_markets,
                    "ece": r.ece,
                    "brier_score": r.brier_score,
                    "computed": r.computed_at.isoformat() if r.computed_at else None,
                }
                for r in records
            ],
        }
