"""
PredictorX — Data Access Layer
CRUD operations for all database tables.
"""

import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional

from sqlalchemy import create_engine, desc, func
from sqlalchemy.orm import sessionmaker, Session

from db.models import (
    Base, PredictionRecord, WeatherForecastRecord, VixSnapshotRecord,
    WhaleSignalRecord, DailyPerformanceRecord, CalibrationSnapshotRecord,
    AlertRecord, MarketCacheRecord, ExternalIntelRecord
)
from core.models import Prediction, VixSnapshot, WhaleSignal

logger = logging.getLogger(__name__)


class Repository:
    """Data access layer for PredictorX."""

    def __init__(self, database_url: str):
        self.engine = create_engine(database_url, echo=False)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        # Enable WAL mode for concurrent reads
        with self.engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")

    def _session(self) -> Session:
        return self.Session()

    # ── Predictions ───────────────────────────────────────

    def save_prediction(self, pred: Prediction) -> int:
        """Save a prediction and return its ID."""
        with self._session() as session:
            record = PredictionRecord(
                strategy=pred.strategy,
                market_ticker=pred.market_ticker,
                market_title=pred.market_title,
                platform=pred.platform,
                predicted_probability=pred.predicted_probability,
                calibrated_probability=pred.calibrated_probability,
                market_price=pred.market_price,
                edge=pred.edge,
                side=pred.side,
                confidence_score=pred.confidence_score,
                confidence_factors=json.dumps(pred.confidence_factors),
                kelly_fraction=pred.kelly_fraction,
                recommended_contracts=pred.recommended_contracts,
                recommended_cost=pred.recommended_cost,
                vix_level=pred.vix_level,
                vix_regime=pred.vix_regime,
                whale_sentiment=pred.whale_sentiment,
                expiry=pred.expiry,
            )
            session.add(record)
            session.commit()
            return record.id

    def get_pending_predictions(self) -> list[PredictionRecord]:
        """Get all unsettled predictions."""
        with self._session() as session:
            return session.query(PredictionRecord).filter(
                PredictionRecord.outcome.is_(None)
            ).order_by(desc(PredictionRecord.created_at)).all()

    def get_recent_predictions(self, limit: int = 50, strategy: str = None) -> list[PredictionRecord]:
        """Get recent predictions, optionally filtered by strategy."""
        with self._session() as session:
            q = session.query(PredictionRecord)
            if strategy:
                q = q.filter(PredictionRecord.strategy == strategy)
            return q.order_by(desc(PredictionRecord.created_at)).limit(limit).all()

    def settle_prediction(self, prediction_id: int, outcome: str, actual_result: str, pnl: float):
        """Mark a prediction as settled."""
        with self._session() as session:
            record = session.query(PredictionRecord).get(prediction_id)
            if record:
                record.outcome = outcome
                record.actual_result = actual_result
                record.pnl = pnl
                record.settled_at = datetime.utcnow()
                session.commit()

    # ── VIX Snapshots ─────────────────────────────────────

    def save_vix_snapshot(self, snapshot: VixSnapshot):
        """Save a VIX data point."""
        with self._session() as session:
            record = VixSnapshotRecord(
                vix_price=snapshot.price,
                regime=snapshot.regime,
                spx_price=snapshot.spx_price,
                spx_change_pct=snapshot.spx_change_pct,
                source=snapshot.source,
            )
            session.add(record)
            session.commit()

    def get_latest_vix(self) -> Optional[VixSnapshotRecord]:
        """Get the most recent VIX snapshot."""
        with self._session() as session:
            return session.query(VixSnapshotRecord).order_by(
                desc(VixSnapshotRecord.fetched_at)
            ).first()

    # ── Whale Signals ─────────────────────────────────────

    def save_whale_signal(self, signal: WhaleSignal):
        """Save a whale trade signal."""
        with self._session() as session:
            record = WhaleSignalRecord(
                wallet_address=signal.wallet_address,
                wallet_alias=signal.wallet_alias,
                whale_category=signal.whale_category,
                market_id=signal.market_id,
                market_name=signal.market_name,
                side=signal.side,
                amount_usd=signal.amount_usd,
                price=signal.price,
                market_sentiment_score=signal.sentiment_score,
            )
            session.add(record)
            session.commit()

    def get_recent_whale_signals(self, hours: int = 24, min_amount: float = 0) -> list[WhaleSignalRecord]:
        """Get recent whale signals."""
        since = datetime.utcnow() - timedelta(hours=hours)
        with self._session() as session:
            q = session.query(WhaleSignalRecord).filter(
                WhaleSignalRecord.detected_at >= since
            )
            if min_amount > 0:
                q = q.filter(WhaleSignalRecord.amount_usd >= min_amount)
            return q.order_by(desc(WhaleSignalRecord.detected_at)).all()

    # ── Performance ───────────────────────────────────────

    def get_performance_summary(self, days: int = 30) -> dict:
        """Get performance summary over the last N days."""
        since = date.today() - timedelta(days=days)
        with self._session() as session:
            records = session.query(PredictionRecord).filter(
                PredictionRecord.settled_at.isnot(None),
                PredictionRecord.created_at >= datetime.combine(since, datetime.min.time()),
            ).all()

            total = len(records)
            wins = sum(1 for r in records if r.outcome == "win")
            total_pnl = sum(r.pnl or 0 for r in records)

            by_strategy = {}
            for r in records:
                s = r.strategy
                if s not in by_strategy:
                    by_strategy[s] = {"count": 0, "wins": 0, "pnl": 0.0}
                by_strategy[s]["count"] += 1
                if r.outcome == "win":
                    by_strategy[s]["wins"] += 1
                by_strategy[s]["pnl"] += r.pnl or 0

            for s in by_strategy:
                c = by_strategy[s]["count"]
                by_strategy[s]["accuracy"] = by_strategy[s]["wins"] / c if c > 0 else 0

            return {
                "total_predictions": total,
                "accuracy": wins / total if total > 0 else 0,
                "total_pnl": round(total_pnl, 2),
                "by_strategy": by_strategy,
            }

    # ── Alerts ────────────────────────────────────────────

    def save_alert(self, alert_type: str, message: str, prediction_id: int = None):
        """Log a sent alert."""
        with self._session() as session:
            record = AlertRecord(
                alert_type=alert_type,
                prediction_id=prediction_id,
                message_text=message,
            )
            session.add(record)
            session.commit()

    # ── Market Cache ──────────────────────────────────────

    def update_market_cache(self, ticker: str, data: dict):
        """Update or insert market cache entry."""
        with self._session() as session:
            record = session.query(MarketCacheRecord).filter_by(ticker=ticker).first()
            if record:
                for k, v in data.items():
                    if hasattr(record, k):
                        setattr(record, k, v)
                record.updated_at = datetime.utcnow()
            else:
                record = MarketCacheRecord(ticker=ticker, **data)
                session.add(record)
            session.commit()

    # ── External Intel ─────────────────────────────────────

    def save_external_intel(self, records: list[dict]):
        """Bulk insert external trader intel entries."""
        with self._session() as session:
            for r in records:
                record = ExternalIntelRecord(
                    source=r.get("source", ""),
                    date=r.get("date", date.today()),
                    ticker=r.get("ticker", ""),
                    level_type=r.get("level_type"),
                    level_price=r.get("level_price"),
                    sentiment=r.get("sentiment"),
                    note=r.get("note"),
                    raw_text=r.get("raw_text"),
                )
                session.add(record)
            session.commit()

    def get_external_intel(self, for_date: date = None, source: str = None) -> list[ExternalIntelRecord]:
        """Fetch external intel entries for a given date."""
        target = for_date or date.today()
        with self._session() as session:
            q = session.query(ExternalIntelRecord).filter(
                ExternalIntelRecord.date == target
            )
            if source:
                q = q.filter(ExternalIntelRecord.source == source)
            return q.order_by(ExternalIntelRecord.created_at.desc()).all()
