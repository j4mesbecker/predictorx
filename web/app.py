"""
PredictorX — FastAPI Application Factory
Web dashboard and API routes.
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from web.routes import dashboard, opportunities, weather, tails, whales, performance, calibration, ws, execute, trades
from config.settings import get_settings

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="PredictorX",
        description="Prediction Intelligence Platform",
        version="1.0.0",
    )

    # Register API routes
    app.include_router(dashboard.router, prefix="/api", tags=["Dashboard"])
    app.include_router(opportunities.router, prefix="/api", tags=["Opportunities"])
    app.include_router(weather.router, prefix="/api", tags=["Weather"])
    app.include_router(tails.router, prefix="/api", tags=["S&P Tails"])
    app.include_router(whales.router, prefix="/api", tags=["Whales"])
    app.include_router(performance.router, prefix="/api", tags=["Performance"])
    app.include_router(calibration.router, prefix="/api", tags=["Calibration"])
    app.include_router(ws.router, tags=["WebSocket"])
    app.include_router(execute.router, prefix="/api", tags=["Execution"])
    app.include_router(trades.router, prefix="/api", tags=["Trades"])

    # Serve static files
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        index = static_dir / "index.html"
        if index.exists():
            return FileResponse(str(index))
        return {"message": "PredictorX API", "docs": "/docs"}

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "PredictorX"}

    logger.info(f"PredictorX web app created: http://{settings.web_host}:{settings.web_port}")
    return app
