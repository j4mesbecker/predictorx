"""
PredictorX — Whale Intelligence API Routes
GET /api/whales — whale trader activity and signals.
"""

from fastapi import APIRouter, Query

from config.settings import get_settings
from db.repository import Repository

router = APIRouter()


@router.get("/whales")
async def get_whale_activity(hours: int = Query(24, ge=1, le=168), min_amount: float = Query(0)):
    """Get recent whale trading signals."""
    settings = get_settings()
    repo = Repository(settings.database_sync_url)
    signals = repo.get_recent_whale_signals(hours=hours, min_amount=min_amount)

    return {
        "count": len(signals),
        "signals": [
            {
                "alias": s.wallet_alias or s.wallet_address[:8],
                "category": s.whale_category,
                "market": s.market_name or s.market_id,
                "side": s.side,
                "amount": s.amount_usd,
                "price": s.price,
                "sentiment": s.market_sentiment_score,
                "detected": s.detected_at.isoformat() if s.detected_at else None,
            }
            for s in signals
        ],
    }


@router.get("/whales/profiles")
async def get_whale_profiles():
    """Get curated whale profiles and rankings."""
    try:
        from adapters.copy_bot import get_curated_whales
        whales = get_curated_whales()

        if not isinstance(whales, dict):
            return {"count": 0, "profiles": []}

        profiles = []
        for addr, info in list(whales.items())[:20]:
            if isinstance(info, dict):
                profiles.append({
                    "address": addr[:12] + "...",
                    "alias": info.get("alias", addr[:8]),
                    "category": info.get("category", "UNKNOWN"),
                    "pnl": info.get("pnl", 0),
                    "win_rate": info.get("win_rate", 0),
                    "total_trades": info.get("total_trades", 0),
                })

        return {"count": len(profiles), "profiles": profiles}

    except Exception:
        return {"count": 0, "profiles": [], "error": "Whale module not available"}
