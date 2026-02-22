"""
Scheduler for periodic recording folder polling.
Detects new recording files and triggers the transcription + processing pipeline.
"""
import asyncio
from datetime import datetime
from typing import Optional, Set

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.recording_watcher import (
    poll_and_process_recordings,
    clear_processed_cache as clear_watcher_cache,
    get_recordings_status
)
from app.logger import get_logger

logger = get_logger(__name__)

# Scheduler instance
_scheduler: Optional[AsyncIOScheduler] = None


async def async_poll_and_process() -> None:
    """Async wrapper for the polling function."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, poll_and_process_recordings)


def start_scheduler() -> AsyncIOScheduler:
    """
    Start the async scheduler for periodic recording polling.
    
    Returns:
        The started scheduler instance
    """
    global _scheduler
    
    if _scheduler is not None:
        logger.warning("Scheduler already running")
        return _scheduler
    
    _scheduler = AsyncIOScheduler()
    
    # Add the polling job
    _scheduler.add_job(
        async_poll_and_process,
        trigger=IntervalTrigger(seconds=settings.recordings_poll_interval),
        id='recordings_polling_job',
        name='Recording Folder Polling',
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )
    
    _scheduler.start()
    logger.info(
        f"Scheduler started. Polling interval: {settings.recordings_poll_interval} seconds"
    )
    
    return _scheduler


def stop_scheduler() -> None:
    """Stop the scheduler gracefully."""
    global _scheduler
    
    if _scheduler is None:
        logger.warning("No scheduler running")
        return
    
    _scheduler.shutdown(wait=True)
    _scheduler = None
    logger.info("Scheduler stopped")


def get_scheduler() -> Optional[AsyncIOScheduler]:
    """Get the current scheduler instance."""
    return _scheduler


def trigger_immediate_poll() -> None:
    """Trigger an immediate poll outside of the schedule."""
    logger.info("Triggering immediate poll")
    poll_and_process_recordings()


def get_scheduler_status() -> dict:
    """
    Get the current status of the scheduler.
    
    Returns:
        Dictionary with scheduler status info
    """
    if _scheduler is None:
        return {
            "running": False,
            "next_run": None,
            "job_count": 0,
            "poll_interval": settings.recordings_poll_interval
        }
    
    jobs = _scheduler.get_jobs()
    next_run = None
    
    for job in jobs:
        if job.id == 'recordings_polling_job':
            next_run = job.next_run_time.isoformat() if job.next_run_time else None
            break
    
    return {
        "running": _scheduler.running,
        "next_run": next_run,
        "job_count": len(jobs),
        "poll_interval": settings.recordings_poll_interval
    }


def clear_processed_cache() -> None:
    """Clear the in-memory processed recordings cache."""
    clear_watcher_cache()
    logger.info("Cleared processed recordings cache")
