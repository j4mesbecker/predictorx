"""
PredictorX — Database Initialization
Creates all tables and runs any needed migrations.
"""

import logging
from config.settings import get_settings
from db.models import init_db

logger = logging.getLogger(__name__)


def initialize_database():
    """Create all tables if they don't exist."""
    settings = get_settings()
    engine = init_db(settings.database_sync_url)
    logger.info(f"Database initialized at {settings.database_path}")
    return engine


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    initialize_database()
    print("Database initialized successfully.")
