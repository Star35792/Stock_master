"""Scheduling entry points; business orchestration lives in services."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.repositories.system import JobLogRepository
from app.services.factor_pipeline_workflow import (
    DAILY_FACTOR_SET_VERSION as DAILY_FACTOR_SET_VERSION,
    DAILY_PRICE_VOLUME_FACTORS as DAILY_PRICE_VOLUME_FACTORS,
    FactorBatchResult as FactorBatchResult,
    run_all_factors as run_all_factors,
)


def create_factor_pipeline_scheduler(
    *,
    lookback_days: int = 14,
    adjust_type: str = "qfq",
    code_version: str = "local",
    symbols: list[str] | None = None,
    hour: int = 18,
    minute: int = 30,
    timezone: str = "Asia/Shanghai",
    session_factory: Callable[[], Session] = SessionLocal,
    job_log_repository: JobLogRepository | None = None,
) -> BlockingScheduler:
    """日级因子增量调度：每个交易日收盘后重算最近 lookback_days 窗口。

    因子值幂等 upsert，滚动重算只更新不重复。窗口保留一定回溯是为了覆盖
    停牌恢复、数据补齐和财务公告生效的延迟。
    """
    if lookback_days < 1:
        raise ValueError("lookback_days must be >= 1")

    def _job() -> None:
        today = date.today()
        run_all_factors(
            start_date=today - timedelta(days=lookback_days),
            end_date=today,
            symbols=symbols,
            adjust_type=adjust_type,
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
        id="factor_pipeline",
        name="Daily factor batch computation",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    return scheduler
