"""
PredictorX — Trade Execution API Route.
POST /api/execute — Place a Kalshi order via FRIDAY or other authorized clients.
Protected by shared secret header.
"""

import logging
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# Shared secret for FRIDAY → PredictorX execution calls
EXECUTE_SECRET = os.environ.get("PREDICTORX_EXECUTE_SECRET", "friday-execute-key")


class ExecuteRequest(BaseModel):
    ticker: str
    side: str  # "yes" or "no"
    contracts: int
    price_cents: int
    strategy: str
    metadata: dict | None = None


class ExecuteResponse(BaseModel):
    status: str
    order_id: str | None = None
    ticker: str
    side: str
    contracts: int
    price_cents: int
    cost: float = 0.0
    kalshi_status: str = ""
    fill_count: int = 0
    reason: str = ""
    error: str = ""


@router.post("/execute", response_model=ExecuteResponse)
async def execute_trade(
    req: ExecuteRequest,
    x_execute_secret: str = Header(alias="X-Execute-Secret", default=""),
):
    """Place a Kalshi order. Requires X-Execute-Secret header."""
    if x_execute_secret != EXECUTE_SECRET:
        raise HTTPException(status_code=403, detail="Invalid execute secret")

    try:
        from pipeline.kalshi_executor import place_order

        result = place_order(
            ticker=req.ticker,
            side=req.side,
            contracts=req.contracts,
            price_cents=req.price_cents,
            strategy=req.strategy,
            metadata=req.metadata or {},
        )

        return ExecuteResponse(
            status=result.get("status", "error"),
            order_id=result.get("order_id"),
            ticker=result.get("ticker", req.ticker),
            side=result.get("side", req.side),
            contracts=result.get("contracts", req.contracts),
            price_cents=result.get("price_cents", req.price_cents),
            cost=result.get("cost", 0.0),
            kalshi_status=result.get("kalshi_status", ""),
            fill_count=result.get("fill_count", 0),
            reason=result.get("reason", ""),
            error=result.get("error", ""),
        )

    except Exception as e:
        logger.error(f"Execute endpoint error: {e}")
        return ExecuteResponse(
            status="error",
            ticker=req.ticker,
            side=req.side,
            contracts=req.contracts,
            price_cents=req.price_cents,
            error=str(e),
        )
