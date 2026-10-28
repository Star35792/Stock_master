"""Application-owned worker; network and database work stays off the event loop."""

import asyncio
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import Settings, settings
from app.database.session import SessionLocal
from app.models.system import JobLog
from app.scheduler.daily_loop import run_daily_loop
from app.scheduler.database_job_lock import database_job_lock
from app.scheduler.factor_pipeline import DAILY_PRICE_VOLUME_FACTORS
from app.services.auto_data_sync import AutoDataSyncService
from app.services.corporate_action_evidence import CorporateActionEvidenceService
from app.services.delist_evidence import DelistEvidenceService
from app.services.turnover_evidence import TurnoverEvidenceService

logger = logging.getLogger(__name__)
_sync_lock = database_job_lock  # Preserve the worker's existing test/operator hook.


def _has_published_closure(db: Session, *, target_date: date) -> bool:
    jobs = db.scalars(
        select(JobLog)
        .where(JobLog.job_name == "daily_loop")
        .order_by(JobLog.started_at.desc(), JobLog.id.desc())
        .limit(50)
    ).all()
    target = target_date.isoformat()
    return any(
        job.status in {"success", "partial"}
        and (job.metadata_ or {}).get("target_trade_date") == target
        and (job.metadata_ or {}).get("publish_eligible") is True
        and (job.metadata_ or {}).get("daily_price_volume_factors")
        == list(DAILY_PRICE_VOLUME_FACTORS)
        for job in jobs
    )


def run_auto_data_sync() -> None:
    # Multiple app instances in one process must not share a dump download or
    # concurrently mutate the same market rows. DuckDB deployment uses one worker.
    if not _sync_lock.acquire(blocking=False):
        return
    target_date: date | None = None
    try:
        with SessionLocal() as db:
            sync_result = AutoDataSyncService().sync(db)
            target_date = sync_result.end_date
            closure_needed = sync_result.advanced or not _has_published_closure(
                db, target_date=sync_result.end_date
            )
        if closure_needed:
            loop_result = run_daily_loop(
                skip_sync=True,
                target_date=sync_result.end_date,
                adjustment_version="v1",
                code_version=Settings().app_code_version,
            )
            if loop_result.failed:
                raise RuntimeError(
                    "daily data-to-factor closure failed after market sync"
                )
    finally:
        if settings.corporate_action_evidence_sync_enabled:
            # Evidence is independent of the trusted daily/factor closure. A
            # supplier outage must not make that closure fail retroactively,
            # and a daily-source outage must not suppress this raw collection.
            try:
                with SessionLocal() as db:
                    outcomes = CorporateActionEvidenceService().collect_pending(db)
                failures = [item for item in outcomes if item["status"] == "failed"]
                if failures:
                    logger.warning(
                        "Corporate-action evidence collection: %s/%s supplier requests failed",
                        len(failures),
                        len(outcomes),
                    )
            except Exception:
                logger.exception("Corporate-action evidence collection failed")
        if settings.delist_evidence_sync_enabled:
            try:
                with SessionLocal() as db:
                    outcomes = DelistEvidenceService().collect_due(db)
                if any(item["status"] != "success" for item in outcomes):
                    logger.warning("Delisting evidence remains incomplete: %s", outcomes)
            except Exception:
                logger.exception("Delisting evidence collection failed")
        if settings.turnover_evidence_sync_enabled and target_date is not None:
            try:
                with SessionLocal() as db:
                    outcomes = TurnoverEvidenceService().collect_pending(
                        db, trade_date=target_date,
                        limit=settings.turnover_evidence_symbols_per_run,
                    )
                failures = sum(item["status"] != "success" for item in outcomes)
                if failures:
                    logger.warning("Turnover evidence: %s/%s symbols unmatched",
                                   failures, len(outcomes))
            except Exception:
                logger.exception("Turnover evidence collection failed")
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
