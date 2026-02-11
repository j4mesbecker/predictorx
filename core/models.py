"""
PredictorX — Core Domain Models
Shared dataclasses used across the entire platform.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Prediction:
    """A single market prediction with confidence scoring."""
    id: Optional[int] = None
    strategy: str = ""                    # "weather", "sp_tail", "bracket_arb"
    market_ticker: str = ""               # Kalshi ticker (e.g., "KXHIGHNY-26FEB10-T42")
    market_title: str = ""
    platform: str = "kalshi"              # "kalshi" or "polymarket"

    # Prediction values
    predicted_probability: float = 0.0    # Our model's raw probability
    calibrated_probability: float = 0.0   # After calibration correction
    market_price: float = 0.0             # Current market implied probability
    edge: float = 0.0                     # calibrated_prob - market_price

    # Confidence
    confidence_score: float = 0.0         # 0.0 to 1.0 composite
    confidence_factors: dict = field(default_factory=dict)

    # Sizing recommendation
    kelly_fraction: float = 0.0
    recommended_contracts: int = 0
    recommended_cost: float = 0.0

    # Metadata
    side: str = ""                        # "yes" or "no"
    expiry: Optional[datetime] = None
    vix_level: Optional[float] = None
    vix_regime: Optional[str] = None
    whale_sentiment: Optional[float] = None  # -1.0 to +1.0
    created_at: datetime = field(default_factory=datetime.utcnow)

    # Outcome tracking
    outcome: Optional[str] = None         # "win", "loss", None (pending)
    actual_result: Optional[str] = None   # "yes" or "no"
    settled_at: Optional[datetime] = None
    pnl: Optional[float] = None

    @property
    def is_actionable(self) -> bool:
        """Whether this prediction has enough edge to act on (aggressive mode)."""
        return self.edge >= 0.04 and self.confidence_score >= 0.55

    @property
    def urgency(self) -> str:
        if self.confidence_score >= 0.85 and self.edge >= 0.08:
            return "HIGH"
        elif self.confidence_score >= 0.65 and self.edge >= 0.04:
            return "MEDIUM"
        return "LOW"


@dataclass
class Opportunity:
    """A ranked trading opportunity for display."""
    rank: int
    prediction: Prediction
    reasons: list[str] = field(default_factory=list)

    @property
    def urgency(self) -> str:
        return self.prediction.urgency


@dataclass
class MarketSnapshot:
    """Current state of a Kalshi market."""
    ticker: str = ""
    title: str = ""
    yes_bid: float = 0.0
    yes_ask: float = 0.0
    no_bid: float = 0.0
    no_ask: float = 0.0
    volume: int = 0
    open_interest: int = 0
    close_time: Optional[str] = None
    status: str = "open"
    result: Optional[str] = None


@dataclass
class WeatherForecast:
    """Multi-source weather forecast for a city/date."""
    city: str = ""
    forecast_date: str = ""
    nws_high: Optional[float] = None
    open_meteo_high: Optional[float] = None
    weatherapi_high: Optional[float] = None
    visualcrossing_high: Optional[float] = None
    consensus_high: Optional[float] = None
    source_agreement: float = 0.0         # 0.0 to 1.0
    forecast_horizon_days: int = 0
    seasonal_bias: float = 0.0
    uhi_adjustment: float = 0.0


@dataclass
class VixSnapshot:
    """VIX regime data point."""
    price: float = 0.0
    regime: str = "UNKNOWN"
    spx_price: Optional[float] = None
    spx_change_pct: Optional[float] = None
    source: str = ""
    fetched_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class WhaleSignal:
    """A whale trade signal from Polymarket."""
    wallet_address: str = ""
    wallet_alias: str = ""
    whale_category: str = ""              # "LEGEND", "ELITE", "SPECIALIST", "RISING"
    market_id: str = ""
    market_name: str = ""
    side: str = ""                        # "BUY" or "SELL"
    amount_usd: float = 0.0
    price: Optional[float] = None
    sentiment_score: Optional[float] = None  # -1.0 to +1.0


@dataclass
class DailyPerformance:
    """Daily performance snapshot."""
    date: str = ""
    total_predictions: int = 0
    correct_predictions: int = 0
    accuracy: float = 0.0
    brier_score: Optional[float] = None
    hypothetical_pnl: float = 0.0
    cumulative_pnl: float = 0.0
    avg_vix: Optional[float] = None
    vix_regime: str = ""
    by_strategy: dict = field(default_factory=dict)
