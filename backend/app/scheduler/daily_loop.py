"""日级数据→因子闭环调度器。

把 ``daily_sync`` 与 ``factor_pipeline`` 串成一次编排运行：
先同步日线并构建 ``market`` 可信层，再滚动计算最近窗口的全部因子。
整次运行写入 ``system.job_log``；sync 失败时不继续执行因子计算。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.session import SessionLocal
from app.lake import CuratedLayer, DataLake
from app.lake.curated import CuratedDatasetResult
from app.repositories.market import SecurityRepository
from app.repositories.system import JobLogRepository
from app.scheduler.daily_sync import run_hithink_daily_sync
from app.scheduler.factor_pipeline import FactorBatchResult, run_all_factors
from app.services import HithinkDailySyncResult
from app.services.adjustment_build import AdjustmentBuildService

logger = logging.getLogger(__name__)


@dataclass
class DailyLoopResult:
    sync_result: HithinkDailySyncResult | None = None
    curated_result: CuratedDatasetResult | None = None
    factor_result: FactorBatchResult | None = None
    sync_error: str | None = None
    curated_error: str | None = None
    factor_error: str | None = None
    failed: bool = False

    @property
    def total_rows(self) -> int:
        sync_rows = self.sync_result.market_build.trusted_count if self.sync_result else 0
        factor_rows = self.factor_result.total_rows if self.factor_result else 0
        return sync_rows + factor_rows

    @property
    def failed_services(self) -> list[str]:
        if self.factor_result is None:
            return []
        return self.factor_result.failed


def _resolve_symbols(
    db: Session,
    symbols: list[str] | None,
    security_repository: SecurityRepository | None = None,
) -> list[str]:
    if symbols is not None:
        normalized = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        if not normalized:
            raise ValueError("symbols must not be empty when provided")
        return normalized
    repo = security_repository or SecurityRepository()
    return repo.get_active_symbols(db)


def run_daily_loop(
    *,
    symbols: list[str] | None = None,
    sync_days: int = 14,
    factor_lookback_days: int = 14,
    sync_adjust_type: str = "none",
    factor_adjust_type: str = "qfq",
    refresh_reference_data: bool = False,
    code_version: str = "local",
    session_factory: Callable[[], Session] = SessionLocal,
    security_repository: SecurityRepository | None = None,
    job_log_repository: JobLogRepository | None = None,
) -> DailyLoopResult:
    """运行一次完整的数据同步→因子计算日级闭环。

    参数 ``symbols`` 为 ``None`` 时从 ``market.security`` 拉取全部 active 证券。
    同步失败时直接结束并标记任务失败；因子管道部分失败时标记 partial。
    """
    repo = job_log_repository or JobLogRepository()
    result = DailyLoopResult()

    with session_factory() as db:
        resolved_symbols = _resolve_symbols(db, symbols, security_repository)
        job = repo.create(
            db,
            job_name="daily_loop",
            job_type="schedule",
            metadata={
                "symbols_count": len(resolved_symbols),
                "sync_days": sync_days,
                "factor_lookback_days": factor_lookback_days,
                "sync_adjust_type": sync_adjust_type,
                "factor_adjust_type": factor_adjust_type,
                "refresh_reference_data": refresh_reference_data,
                "code_version": code_version,
            },
        )
        db.commit()

        try:
            sync_result = run_hithink_daily_sync(
                symbols=resolved_symbols,
                days=sync_days,
                adjust_type=sync_adjust_type,
                refresh_reference_data=refresh_reference_data,
                session_factory=session_factory,
                job_log_repository=repo,
            )
            result.sync_result = sync_result
            job.metadata_ = {
                **(job.metadata_ or {}),
                "sync_batch_no": sync_result.collection.batch_no,
                "sync_status": sync_result.collection.status,
            }
        except Exception as exc:  # noqa: BLE001 - log and surface
            error_message = f"sync failed: {type(exc).__name__}: {exc}"
            result.sync_error = error_message
            result.failed = True
            repo.finish(
                db,
                job_log=job,
                status="failed",
                error_message=error_message,
                metadata=job.metadata_,
            )
            db.commit()
            logger.exception("Daily loop failed at sync stage")
            return result

        if settings.factor_reads_curated and factor_adjust_type == "qfq":
            try:
                if sync_adjust_type != "none":
                    raise ValueError(
                        "factor_adjust_type='qfq' with factor_reads_curated requires "
                        f"sync_adjust_type='none', got '{sync_adjust_type}'"
                    )
                adjustment = AdjustmentBuildService().rebuild_qfq(
                    db,
                    source_name="daily_loop",
                    version=code_version,
                )
                db_path = str(Path(settings.metadata_db_path).resolve())
                curated_result = CuratedLayer(DataLake()).build_prices_qfq_from_db(
                    db_path
                )
                result.curated_result = curated_result
                job.metadata_ = {
                    **(job.metadata_ or {}),
                    "curated_rebuilt": True,
                    "adjustment_factor_rows": adjustment.factor_rows,
                    "adjustment_qfq_bar_rows": adjustment.qfq_bar_rows,
                    "curated_path": curated_result.path,
                    "curated_row_count": curated_result.row_count,
                    "curated_min_date": curated_result.min_date,
                    "curated_max_date": curated_result.max_date,
                }
            except Exception as exc:  # noqa: BLE001 - log and surface
                error_message = f"curated build failed: {type(exc).__name__}: {exc}"
                result.curated_error = error_message
                result.failed = True
                repo.finish(
                    db,
                    job_log=job,
                    status="failed",
                    error_message=error_message,
                    metadata=job.metadata_,
                )
                db.commit()
                logger.exception("Daily loop failed at curated build stage")
                return result

        try:
            today = date.today()
            factor_result = run_all_factors(
                start_date=today - timedelta(days=factor_lookback_days),
                end_date=today,
                symbols=resolved_symbols,
                adjust_type=factor_adjust_type,
                code_version=code_version,
                session_factory=session_factory,
                job_log_repository=repo,
            )
            result.factor_result = factor_result
            job.rows_inserted = factor_result.total_rows
            if factor_result.failed:
                job.status = "partial"
                job.error_message = (
                    f"factor pipeline partial; failed services: {factor_result.failed}"
                )
            else:
                job.status = "success"
            job.metadata_ = {
                **(job.metadata_ or {}),
                "failed_services": factor_result.failed,
                "services": {
                    name: svc.get("total_rows", 0) for name, svc in factor_result.services.items()
                },
            }
        except Exception as exc:  # noqa: BLE001 - log and surface
            error_message = f"factor pipeline failed: {type(exc).__name__}: {exc}"
            result.factor_error = error_message
            result.failed = True
            job.status = "failed"
            job.error_message = error_message

        repo.finish(
            db,
            job_log=job,
            status=job.status,
            error_message=job.error_message,
            rows_inserted=job.rows_inserted,
            metadata=job.metadata_,
        )
        db.commit()

    logger.info(
        "Daily loop completed: failed=%s sync_trusted=%s factor_rows=%s",
        result.failed,
        result.sync_result.market_build.trusted_count if result.sync_result else 0,
        result.factor_result.total_rows if result.factor_result else 0,
    )
    return result


def create_daily_loop_scheduler(
    *,
    symbols: list[str] | None = None,
    sync_days: int = 14,
    factor_lookback_days: int = 14,
    sync_adjust_type: str = "none",
    factor_adjust_type: str = "qfq",
    refresh_reference_data: bool = False,
    code_version: str = "local",
    hour: int = 18,
    minute: int = 40,
    timezone: str = "Asia/Shanghai",
    session_factory: Callable[[], Session] = SessionLocal,
    job_log_repository: JobLogRepository | None = None,
) -> BlockingScheduler:
    """日级数据→因子闭环调度器：每个交易日收盘后自动运行。"""
    if sync_days < 1:
        raise ValueError("sync_days must be >= 1")
    if factor_lookback_days < 1:
        raise ValueError("factor_lookback_days must be >= 1")

    def _job() -> None:
        run_daily_loop(
            symbols=symbols,
            sync_days=sync_days,
            factor_lookback_days=factor_lookback_days,
            sync_adjust_type=sync_adjust_type,
            factor_adjust_type=factor_adjust_type,
            refresh_reference_data=refresh_reference_data,
            code_version=code_version,
            session_factory=session_factory,
            job_log_repository=job_log_repository,
        )

    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        _job,
        trigger="cron",
        day_of_week="mon-fri",
        hour=hour,
        minute=minute,
        id="daily_loop",
        name="Daily data to factor closed loop",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    return scheduler
