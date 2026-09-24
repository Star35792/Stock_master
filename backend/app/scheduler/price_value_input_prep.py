"""Prepare same-day research inputs at 21:00 Asia/Shanghai."""

import asyncio
import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config.settings import Settings
from app.database.session import SessionLocal
from app.scheduler.database_job_lock import database_job_lock
from app.services.price_value_input_prep import PriceValueInputPrepService

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def seconds_until_next_attempt(now: datetime) -> float:
    if now.tzinfo is None:
        raise ValueError("scheduler clock must include timezone")
    local = now.astimezone(SHANGHAI)
    if local.hour < 21:
        next_run = datetime.combine(local.date(), time(21), SHANGHAI)
    else:
        # A same-evening failure may recover after the hourly daily-data sync.
        next_run = local + timedelta(minutes=30)
        if next_run.date() != local.date():
            next_run = datetime.combine(local.date() + timedelta(days=1), time(21), SHANGHAI)
    return max((next_run - local).total_seconds(), 0)


def run_price_value_input_prep() -> None:
    with database_job_lock:
        with SessionLocal() as db:
            PriceValueInputPrepService().run(db)


class PriceValueInputPrepWorker:
    def __init__(self, config: Settings) -> None:
        self.config = config
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self.config.price_value_input_prep_enabled and self._task is None:
            self._task = asyncio.create_task(self._run(), name="price-value-input-prep")

    async def _once(self) -> None:
        try:
            await asyncio.to_thread(run_price_value_input_prep)
        except Exception as exc:
            logger.error("Price/value input preparation failed (%s)", type(exc).__name__)

    async def _run(self) -> None:
        local = datetime.now(SHANGHAI)
        if local.hour >= 21:
            await self._once()
        while not self._stop.is_set():
            timeout = seconds_until_next_attempt(datetime.now(SHANGHAI))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=timeout)
            except TimeoutError:
                await self._once()

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
