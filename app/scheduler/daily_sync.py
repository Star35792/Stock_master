import logging
from collections.abc import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.services import HithinkDailySyncResult, HithinkDailySyncService

logger = logging.getLogger(__name__)


def run_hithink_daily_sync(
    *,
    symbols: list[str],
    days: int = 14,
    adjust_type: str = "none",
    refresh_reference_data: bool = False,
    service: HithinkDailySyncService | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> HithinkDailySyncResult:
    with session_factory() as db:
        result = (service or HithinkDailySyncService()).sync(
            db,
            symbols=symbols,
            days=days,
            adjust_type=adjust_type,
            refresh_reference_data=refresh_reference_data,
        )

    logger.info(
        "HiThink daily sync completed: batch=%s accepted=%s rejected=%s trusted=%s issues=%s",
        result.collection.batch_no,
        result.collection.accepted_count,
        result.collection.rejected_count,
        result.market_build.trusted_count,
        result.market_build.issue_count,
    )
    return result


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
