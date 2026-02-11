"""
PredictorX — Composite Confidence Scorer
Combines 5 weighted factors into a single 0.0-1.0 score.
"""

import logging
from core.models import Prediction
from config.constants import CONFIDENCE_WEIGHTS

logger = logging.getLogger(__name__)


def compute_confidence(prediction: Prediction, context: dict = None) -> float:
    """
    Compute composite confidence score (0.0 to 1.0).

    Factors and weights:
    1. Model agreement (0.30) — how many data sources agree
    2. Historical accuracy (0.25) — calibration curve reliability
    3. Edge magnitude (0.20) — bigger edge = more confidence
    4. Data quality (0.15) — freshness, completeness
    5. Whale alignment (0.10) — do whales agree with our prediction
    """
    if context is None:
        context = prediction.confidence_factors

    factors = {}

    # 1. Model agreement
    factors["model_agreement"] = context.get("model_agreement", 0.5)

    # 2. Historical accuracy
    factors["historical_accuracy"] = context.get("historical_accuracy", 0.5)

    # 3. Edge magnitude (normalized: 0c=0, 20c+=1.0)
    edge = abs(prediction.edge)
    factors["edge_magnitude"] = min(1.0, edge / 0.20)

    # 4. Data quality
    factors["data_quality"] = context.get("data_quality", 0.5)

    # 5. Whale alignment (-1 to +1 mapped to 0 to 1)
    whale = context.get("whale_alignment")
    if whale is not None:
        factors["whale_alignment"] = whale
    elif prediction.whale_sentiment is not None:
        ws = prediction.whale_sentiment
        if prediction.side == "yes":
            factors["whale_alignment"] = (ws + 1) / 2
        else:
            factors["whale_alignment"] = (-ws + 1) / 2
    else:
        factors["whale_alignment"] = 0.5  # Neutral when no data

    # Compute weighted sum
    score = sum(
        CONFIDENCE_WEIGHTS.get(k, 0) * factors.get(k, 0.5)
        for k in CONFIDENCE_WEIGHTS
    )

    score = round(min(1.0, max(0.0, score)), 3)
    prediction.confidence_score = score
    prediction.confidence_factors.update(factors)

    return score


def score_predictions(predictions: list[Prediction], context: dict = None) -> list[Prediction]:
    """Score a list of predictions and return them sorted by confidence."""
    for pred in predictions:
        compute_confidence(pred, context)

    return sorted(predictions, key=lambda p: p.confidence_score, reverse=True)
