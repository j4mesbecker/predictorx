"""
PredictorX — WebSocket Route
/ws/live — real-time updates for the dashboard.
"""

import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config.settings import get_settings
from db.repository import Repository

router = APIRouter()
logger = logging.getLogger(__name__)

# Connected clients
_clients: list[WebSocket] = []


@router.websocket("/ws/live")
async def websocket_live(ws: WebSocket):
    """WebSocket endpoint for live dashboard updates."""
    await ws.accept()
    _clients.append(ws)
    logger.info(f"WebSocket client connected ({len(_clients)} total)")

    try:
        # Send initial state
        data = _get_live_data()
        await ws.send_json({"type": "init", "data": data})

        # Keep connection alive and send periodic updates
        while True:
            await asyncio.sleep(30)
            data = _get_live_data()
            await ws.send_json({"type": "update", "data": data})

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"WebSocket error: {e}")
    finally:
        if ws in _clients:
            _clients.remove(ws)
        logger.info(f"WebSocket client disconnected ({len(_clients)} remaining)")


async def broadcast(event_type: str, data: dict):
    """Broadcast an event to all connected WebSocket clients."""
    if not _clients:
        return

    message = json.dumps({"type": event_type, "data": data, "timestamp": datetime.utcnow().isoformat()})
    disconnected = []

    for client in _clients:
        try:
            await client.send_text(message)
        except Exception:
            disconnected.append(client)

    for client in disconnected:
        _clients.remove(client)


def _get_live_data() -> dict:
    """Get current live data for WebSocket clients."""
    try:
        settings = get_settings()
        repo = Repository(settings.database_sync_url)

        latest_vix = repo.get_latest_vix()
        perf = repo.get_performance_summary(days=1)

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "vix": {
                "price": latest_vix.vix_price if latest_vix else None,
                "regime": latest_vix.regime if latest_vix else "UNKNOWN",
            },
            "today": {
                "predictions": perf.get("total_predictions", 0),
                "accuracy": round(perf.get("accuracy", 0), 4),
                "pnl": perf.get("total_pnl", 0),
            },
            "connected_clients": len(_clients),
        }
    except Exception:
        return {"timestamp": datetime.utcnow().isoformat(), "error": "Data unavailable"}
