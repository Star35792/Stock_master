"""Application-owned worker; network and database work stays off the event loop."""

import asyncio
import logging
from threading import Lock

from app.config.settings import Settings
from app.database.session import SessionLocal
from app.services.auto_data_sync import AutoDataSyncService

logger = logging.getLogger(__name__)
_sync_lock = Lock()


def run_auto_data_sync() -> None:
    # Multiple app instances in one process must not share a dump download or
    # concurrently mutate the same market rows. DuckDB deployment uses one worker.
    if not _sync_lock.acquire(blocking=False):
        return
    try:
        with SessionLocal() as db:
            AutoDataSyncService().sync(db)
    finally:
        _sync_lock.release()


class AutoDataSyncWorker:
    def __init__(self, config: Settings) -> None:
        self.config = config
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if not self.config.auto_data_sync_enabled:
            return
        if not self.config.hithink_finance_api_key:
            logger.warning("Auto data sync skipped: HITHINK_FINANCE_API_KEY is not configured")
            return
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="auto-data-sync")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(run_auto_data_sync)
            except Exception as exc:
                logger.error(
                    "Auto data sync failed (%s); will retry next interval", type(exc).__name__
                )
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.config.auto_data_sync_interval_seconds
                )
            except TimeoutError:
                pass

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            # Do not cancel a thread while it is committing DuckDB writes.
            await self._task
