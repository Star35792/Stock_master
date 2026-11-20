"""Scheduling entry points; business orchestration lives in services."""

from __future__ import annotations

from collections.abc import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.repositories.system import JobLogRepository
from app.services.daily_data_workflow import (
    CORE_PRICE_FACTORS as CORE_PRICE_FACTORS,
    DailyLoopResult as DailyLoopResult,
    _compute_market_qfq_checksum as _compute_market_qfq_checksum,
    _gather_certification_metadata as _gather_certification_metadata,
    _market_qfq_result as _market_qfq_result,
    _publish_and_activate_curated as _publish_and_activate_curated,
    run_daily_loop as run_daily_loop,
)


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
