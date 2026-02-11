"""
PredictorX — Pipeline Runner
Starts and manages the data pipeline with graceful shutdown.
"""

import asyncio
import logging

from pipeline.scheduler import create_scheduler

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Manages the APScheduler lifecycle."""

    def __init__(self):
        self._scheduler = None

    async def start(self):
        """Start the data pipeline scheduler."""
        logger.info("Starting PredictorX data pipeline...")
        self._scheduler = create_scheduler()
        self._scheduler.start()
        logger.info("Data pipeline running")

        # Log all registered jobs
        for job in self._scheduler.get_jobs():
            logger.info(f"  Job: {job.name} ({job.id}) — next run: {job.next_run_time}")

    async def stop(self):
        """Stop the pipeline gracefully."""
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            logger.info("Data pipeline stopped")

    @property
    def is_running(self) -> bool:
        return self._scheduler is not None and self._scheduler.running

    def get_job_status(self) -> list[dict]:
        """Get status of all scheduled jobs."""
        if not self._scheduler:
            return []

        jobs = []
        for job in self._scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else "paused",
            })
        return jobs


async def run_pipeline():
    """Run the pipeline standalone (for testing)."""
    runner = PipelineRunner()
    await runner.start()

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, asyncio.CancelledError):
        await runner.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_pipeline())
