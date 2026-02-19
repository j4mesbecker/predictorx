"""
PredictorX — Trade Lookup API
Allows FRIDAY to look up pending trades and execute batches.
"""

import logging
import os

from fastapi import APIRouter, Header, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter()

EXECUTE_SECRET = os.getenv("PREDICTORX_EXECUTE_SECRET", "friday-execute-key")


@router.get("/pending-trades/{trade_id}")
async def get_pending_trade(trade_id: str):
    """Look up a pending trade by ID. Used by FRIDAY's callback handler."""
    from telegram.trade_approvals import _pending_trades

    trade = _pending_trades.get(trade_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found or expired")

    return {"trade_id": trade_id, "trade": trade}


@router.post("/execute-batch/{batch_id}")
async def execute_batch(
    batch_id: str,
    x_execute_secret: str = Header(alias="X-Execute-Secret"),
):
    """Execute a pending batch (or single trade). Called by FRIDAY on APPROVE."""
    if x_execute_secret != EXECUTE_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    from telegram.trade_approvals import _pending_trades
    from pipeline.kalshi_executor import place_order

    trade = _pending_trades.get(batch_id)
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found or expired")

    if trade["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Trade already {trade['status']}")

    trade["status"] = "executing"

    if trade.get("type") == "batch":
        results = []
        for t in trade["trades"]:
            try:
                result = place_order(
                    ticker=t["ticker"],
                    side=t["side"],
                    contracts=t["contracts"],
                    price_cents=t["price_cents"],
                    strategy=trade["strategy"],
                    metadata=t.get("metadata", {}),
                )
            except Exception as e:
                logger.error(f"Batch trade {t['ticker']} failed: {e}")
                result = {"status": "error", "error": str(e), "ticker": t["ticker"], "cost": 0}
            results.append(result)

        filled = sum(1 for r in results if r.get("status") == "filled")
        total_cost = sum(r.get("cost", 0) for r in results if r.get("status") == "filled")
        trade["status"] = "executed"
        del _pending_trades[batch_id]

        return {"filled": filled, "total": len(results), "total_cost": total_cost, "results": results}
    else:
        try:
            result = place_order(
                ticker=trade["ticker"],
                side=trade["side"],
                contracts=trade["contracts"],
                price_cents=trade["price_cents"],
                strategy=trade["strategy"],
                metadata=trade.get("metadata", {}),
            )
        except Exception as e:
            result = {"status": "error", "error": str(e)}

        trade["status"] = "executed"
        del _pending_trades[batch_id]

        return {"filled": 1 if result.get("status") == "filled" else 0, "total": 1,
                "total_cost": result.get("cost", 0), "results": [result]}
