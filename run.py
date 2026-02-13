"""
PredictorX — Main Entry Point
Starts the web dashboard, Telegram bot, and data pipeline concurrently.

Usage:
    python run.py              # Start everything
    python run.py --web-only   # Web dashboard only
    python run.py --bot-only   # Telegram bot only
    python run.py --seed       # Seed database and exit
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import get_settings
from adapters.paths import setup_paths
from db.migrations import initialize_database

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/logs/predictorx.log", mode="a"),
    ],
)
logger = logging.getLogger("predictorx")


async def start_web(settings):
    """Start the FastAPI web dashboard."""
    import uvicorn
    from web.app import create_app

    app = create_app()
    config = uvicorn.Config(
        app,
        host=settings.web_host,
        port=settings.web_port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    logger.info(f"Web dashboard: http://{settings.web_host}:{settings.web_port}")
    await server.serve()


async def start_bot():
    """Start the Telegram bot polling loop."""
    from telegram.bot import get_bot
    from telegram.commands import register_all_commands
    from telegram.trade_approvals import register_trade_callbacks

    bot = get_bot()
    if not bot.configured:
        logger.warning("Telegram not configured — bot disabled")
        logger.warning("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return

    register_all_commands()
    register_trade_callbacks(bot)
    logger.info("Starting PredictorX Telegram bot...")
    await bot.start_polling()


async def start_pipeline():
    """Start the data pipeline scheduler."""
    from pipeline.runner import PipelineRunner

    runner = PipelineRunner()
    await runner.start()

    # Keep running until cancelled
    try:
        while True:
            await asyncio.sleep(60)
    except asyncio.CancelledError:
        await runner.stop()


async def run_all():
    """Start all services concurrently."""
    settings = get_settings()
    settings.ensure_dirs()

    # Initialize database
    initialize_database()
    logger.info("Database initialized")

    # Setup adapter paths
    setup_paths()
    logger.info("Adapter paths configured")

    # Run seed if first time
    _maybe_seed()

    logger.info("=" * 60)
    logger.info("  PredictorX — Prediction Intelligence Platform")
    logger.info("=" * 60)
    logger.info(f"  Web:      http://{settings.web_host}:{settings.web_port}")
    logger.info(f"  Telegram: {'Enabled' if settings.telegram_configured else 'Disabled'}")
    logger.info(f"  Kalshi:   {'Configured' if settings.kalshi_configured else 'Not configured'}")
    logger.info(f"  Database: {settings.database_path}")
    logger.info("=" * 60)

    # Create tasks
    tasks = [
        asyncio.create_task(start_web(settings), name="web"),
        asyncio.create_task(start_pipeline(), name="pipeline"),
    ]

    if settings.telegram_configured:
        tasks.append(asyncio.create_task(start_bot(), name="telegram"))

    # Handle graceful shutdown
    loop = asyncio.get_running_loop()

    def shutdown_handler():
        logger.info("Shutting down PredictorX...")
        for task in tasks:
            task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_handler)

    # Wait for all tasks
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        pass

    logger.info("PredictorX stopped")


def _maybe_seed():
    """Run database seeding if the DB appears empty."""
    try:
        from db.repository import Repository
        settings = get_settings()
        repo = Repository(settings.database_sync_url)

        # Check if we have any calibration data
        from db.models import CalibrationSnapshotRecord
        with repo._session() as session:
            count = session.query(CalibrationSnapshotRecord).count()
            if count == 0:
                logger.info("Empty database detected — running seed...")
                from db.seed import run_seed
                run_seed()
    except Exception as e:
        logger.warning(f"Seed check skipped: {e}")


def main():
    args = sys.argv[1:]

    if "--seed" in args:
        settings = get_settings()
        settings.ensure_dirs()
        initialize_database()
        setup_paths()
        from db.seed import run_seed
        run_seed()
        return

    if "--web-only" in args:
        settings = get_settings()
        settings.ensure_dirs()
        initialize_database()

        async def web_only():
            await start_web(settings)

        asyncio.run(web_only())
        return

    if "--bot-only" in args:
        settings = get_settings()
        settings.ensure_dirs()
        initialize_database()
        setup_paths()
        asyncio.run(start_bot())
        return

    asyncio.run(run_all())


if __name__ == "__main__":
    main()
