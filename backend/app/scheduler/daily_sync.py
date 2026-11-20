"""Scheduling entry points; business orchestration lives in services."""

from __future__ import annotations

from apscheduler.schedulers.blocking import BlockingScheduler

from app.services.daily_sync_workflow import (
    run_hithink_daily_sync as run_hithink_daily_sync,
    run_recent_data_completion as run_recent_data_completion,
)


def create_daily_sync_scheduler(
    *,
    symbols: list[str],
    hour: int = 18,
    minute: int = 10,
    timezone: str = "Asia/Shanghai",
    days: int = 14,
    adjust_type: str = "none",
    refresh_reference_data: bool = False,
) -> BlockingScheduler:
    normalized_symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if not normalized_symbols:
        raise ValueError("symbols must not be empty")
    if days < 1:
        raise ValueError("days must be greater than or equal to 1")

    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        run_hithink_daily_sync,
        trigger="cron",
        day_of_week="mon-fri",
        hour=hour,
        minute=minute,
        id="hithink_daily_sync",
        name="HiThink daily market data sync",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
        kwargs={
            "symbols": normalized_symbols,
            "days": days,
            "adjust_type": adjust_type,
            "refresh_reference_data": refresh_reference_data,
        },
    )
    return scheduler


def create_recent_data_completion_scheduler(
    *,
    symbols: list[str],
    hour: int = 18,
    minute: int = 20,
    timezone: str = "Asia/Shanghai",
    days: int = 14,
    adjust_type: str = "qfq",
    refresh_reference_data: bool = False,
) -> BlockingScheduler:
    normalized_symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
    if not normalized_symbols:
        raise ValueError("symbols must not be empty")
    if days < 1:
        raise ValueError("days must be greater than or equal to 1")
    if adjust_type != "qfq":
        raise ValueError("recent data completion currently supports qfq only")

    scheduler = BlockingScheduler(timezone=timezone)
    scheduler.add_job(
        run_recent_data_completion,
        trigger="cron",
        day_of_week="mon-fri",
        hour=hour,
        minute=minute,
        id="recent_data_completion",
        name="Recent trusted daily data completion",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
        kwargs={
            "symbols": normalized_symbols,
            "days": days,
            "adjust_type": adjust_type,
            "refresh_reference_data": refresh_reference_data,
        },
    )
    return scheduler
