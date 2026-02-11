"""
PredictorX — Database Seeding
Imports existing calibration data and backtest results from source repos.
"""

import json
import logging
from datetime import datetime, date
from pathlib import Path

from config.settings import get_settings
from db.repository import Repository
from db.models import CalibrationSnapshotRecord, DailyPerformanceRecord

logger = logging.getLogger(__name__)


def seed_calibration_data(repo: Repository):
    """Import calibration.json from polymarket-trader."""
    calibration_paths = [
        Path.home() / "Desktop" / "polymarket-trader" / "calibration.json",
        Path.home() / "Desktop" / "polymarket-trader" / "src" / "calibration.json",
    ]

    cal_path = None
    for p in calibration_paths:
        if p.exists():
            cal_path = p
            break

    if not cal_path:
        logger.warning("No calibration.json found, skipping calibration seed")
        return

    try:
        with open(cal_path) as f:
            data = json.load(f)

        with repo._session() as session:
            # Check if we already have calibration data
            existing = session.query(CalibrationSnapshotRecord).first()
            if existing:
                logger.info("Calibration data already seeded")
                return

            # Extract calibration curve
            bins = data.get("calibration_bins", data.get("bins", []))
            rates = data.get("actual_rates", data.get("rates", []))
            counts = data.get("sample_counts", data.get("counts", []))
            total = data.get("total_markets", data.get("total", 0))

            record = CalibrationSnapshotRecord(
                strategy="weather",
                total_markets=total,
                ece=data.get("ece"),
                brier_score=data.get("brier_score"),
                predicted_bins=json.dumps(bins),
                actual_rates=json.dumps(rates),
                sample_counts=json.dumps(counts),
            )
            session.add(record)
            session.commit()

        logger.info(f"Seeded calibration data: {total} markets from {cal_path.name}")

    except Exception as e:
        logger.error(f"Calibration seed error: {e}")


def seed_tail_backtest(repo: Repository):
    """Import S&P tail backtest results from kalshi_data."""
    backtest_paths = [
        Path.home() / "Desktop" / "kalshi_data" / "sp500_tail_analysis.py",
        Path.home() / "Desktop" / "kalshi_data" / "data",
    ]

    # The tail probabilities are already in our constants — just log it
    from config.constants import TAIL_PROB
    logger.info(f"Tail probability table loaded from constants ({len(TAIL_PROB)} regimes)")

    # Try to import any existing CSV backtest data
    csv_path = Path.home() / "Desktop" / "kalshi_data" / "data" / "sp500_daily.csv"
    if csv_path.exists():
        logger.info(f"S&P 500 daily data available at: {csv_path}")
    else:
        logger.info("No S&P 500 CSV data found (will fetch via pipeline)")


def seed_whale_profiles(repo: Repository):
    """Log available whale intelligence data."""
    curated_path = Path.home() / "Desktop" / "polymarket-copy-bot" / "src" / "whales" / "curated.py"
    if curated_path.exists():
        logger.info(f"Whale profiles available at: {curated_path}")
    else:
        logger.info("Whale curated profiles not found")


def run_seed():
    """Run all seeding operations."""
    logger.info("Starting database seed...")
    settings = get_settings()
    repo = Repository(settings.database_sync_url)

    seed_calibration_data(repo)
    seed_tail_backtest(repo)
    seed_whale_profiles(repo)

    logger.info("Database seed complete")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_seed()
