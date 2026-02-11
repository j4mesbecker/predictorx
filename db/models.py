"""
PredictorX — SQLAlchemy ORM Models
8 tables for predictions, weather, VIX, whales, performance, calibration, alerts, market cache.
"""

from datetime import datetime

from sqlalchemy import (
    Column, Integer, Float, String, Text, Boolean, DateTime, Date,
    Index, ForeignKey, create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


class PredictionRecord(Base):
    __tablename__ = "predictions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy = Column(String, nullable=False, index=True)
    market_ticker = Column(String, nullable=False, index=True)
    market_title = Column(String)
    platform = Column(String, default="kalshi")

    # Prediction values
    predicted_probability = Column(Float, nullable=False)
    calibrated_probability = Column(Float)
    market_price = Column(Float, nullable=False)
    edge = Column(Float, nullable=False)
    side = Column(String, nullable=False)

    # Confidence
    confidence_score = Column(Float, nullable=False)
    confidence_factors = Column(Text)  # JSON

    # Sizing
    kelly_fraction = Column(Float)
    recommended_contracts = Column(Integer)
    recommended_cost = Column(Float)

    # Context
    vix_level = Column(Float)
    vix_regime = Column(String)
    whale_sentiment = Column(Float)

    # Timing
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    expiry = Column(DateTime)

    # Outcome
    outcome = Column(String, index=True)  # "win", "loss", None
    actual_result = Column(String)
    settled_at = Column(DateTime)
    pnl = Column(Float)


class WeatherForecastRecord(Base):
    __tablename__ = "weather_forecasts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    city = Column(String, nullable=False)
    forecast_date = Column(Date, nullable=False)

    nws_high = Column(Float)
    open_meteo_high = Column(Float)
    weatherapi_high = Column(Float)
    visualcrossing_high = Column(Float)
    consensus_high = Column(Float)

    source_agreement = Column(Float)
    forecast_horizon_days = Column(Integer)
    horizon_decay_factor = Column(Float)
    seasonal_bias = Column(Float)
    uhi_adjustment = Column(Float)

    kalshi_bracket_prices = Column(Text)  # JSON

    fetched_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_weather_city_date", "city", "forecast_date"),
    )


class VixSnapshotRecord(Base):
    __tablename__ = "vix_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vix_price = Column(Float, nullable=False)
    regime = Column(String, nullable=False)
    spx_price = Column(Float)
    spx_change_pct = Column(Float)
    source = Column(String)
    fetched_at = Column(DateTime, default=datetime.utcnow, index=True)


class WhaleSignalRecord(Base):
    __tablename__ = "whale_signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    wallet_address = Column(String, nullable=False)
    wallet_alias = Column(String)
    whale_category = Column(String)

    market_id = Column(String, nullable=False, index=True)
    market_name = Column(String)
    side = Column(String, nullable=False)
    amount_usd = Column(Float, nullable=False)
    price = Column(Float)

    market_sentiment_score = Column(Float)
    detected_at = Column(DateTime, default=datetime.utcnow, index=True)


class DailyPerformanceRecord(Base):
    __tablename__ = "daily_performance"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, unique=True, nullable=False)

    total_predictions = Column(Integer, default=0)
    correct_predictions = Column(Integer, default=0)
    accuracy = Column(Float)
    brier_score = Column(Float)
    log_loss = Column(Float)

    weather_predictions = Column(Integer, default=0)
    weather_correct = Column(Integer, default=0)
    weather_accuracy = Column(Float)
    sp_tail_predictions = Column(Integer, default=0)
    sp_tail_correct = Column(Integer, default=0)
    sp_tail_accuracy = Column(Float)
    arb_predictions = Column(Integer, default=0)
    arb_correct = Column(Integer, default=0)

    hypothetical_pnl = Column(Float, default=0.0)
    cumulative_pnl = Column(Float, default=0.0)

    avg_vix = Column(Float)
    vix_regime = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)


class CalibrationSnapshotRecord(Base):
    __tablename__ = "calibration_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy = Column(String, nullable=False)

    predicted_bins = Column(Text)  # JSON array
    actual_rates = Column(Text)    # JSON array
    sample_counts = Column(Text)   # JSON array

    total_markets = Column(Integer)
    ece = Column(Float)
    brier_score = Column(Float)

    computed_at = Column(DateTime, default=datetime.utcnow)


class AlertRecord(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_type = Column(String, nullable=False)  # "opportunity", "morning_scan", "whale", "regime_change"
    prediction_id = Column(Integer, ForeignKey("predictions.id"), nullable=True)
    message_text = Column(Text)
    sent_at = Column(DateTime, default=datetime.utcnow)
    delivered = Column(Boolean, default=True)


class MarketCacheRecord(Base):
    __tablename__ = "market_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, unique=True, nullable=False)
    title = Column(String)
    category = Column(String)
    yes_bid = Column(Float)
    yes_ask = Column(Float)
    volume = Column(Integer)
    open_interest = Column(Integer)
    close_time = Column(DateTime)
    status = Column(String)
    result = Column(String)
    updated_at = Column(DateTime, default=datetime.utcnow)


def init_db(database_url: str):
    """Create all tables."""
    engine = create_engine(database_url, echo=False)
    Base.metadata.create_all(engine)
    return engine


def get_session_factory(database_url: str):
    """Get a session factory for the given database URL."""
    engine = create_engine(database_url, echo=False)
    return sessionmaker(bind=engine)
