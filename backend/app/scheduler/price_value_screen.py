"""Run the research screen at 00:00 Asia/Shanghai while the API is alive."""

import asyncio
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import Settings
from app.database.session import SessionLocal
from app.scheduler.database_job_lock import database_job_lock
from app.services.price_value_screen import PriceValueScreenService

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def seconds_until_next_midnight(now: datetime) -> float:
    if now.tzinfo is None:
        raise ValueError("scheduler clock must include timezone")
    local = now.astimezone(SHANGHAI)
    next_midnight = datetime.combine(local.date() + timedelta(days=1), time.min, SHANGHAI)
    return (next_midnight - local).total_seconds()


def run_price_value_screen() -> None:
    with database_job_lock:
        with SessionLocal() as db:
            PriceValueScreenService().run(db)


class PriceValueScreenWorker:
    def __init__(self, config: Settings) -> None:
        self.config = config
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self.config.price_value_screen_enabled and self._task is None:
            self._task = asyncio.create_task(self._run(), name="price-value-screen")

    async def _once(self) -> None:
        try:
            await asyncio.to_thread(run_price_value_screen)
        except Exception:
            logger.exception("Price/value research screen failed")

    async def _run(self) -> None:
        # Startup catch-up is idempotent: unchanged snapshots reuse the artifact.
        await self._once()
        while not self._stop.is_set():
            # Refresh changed upstream inputs during the day; midnight remains
            # an exact scheduled boundary for the previous completed session.
            timeout = min(seconds_until_next_midnight(datetime.now(SHANGHAI)), 3600)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=timeout)
            except TimeoutError:
                await self._once()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
