import logging
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.repositories.system import JobLogRepository
from app.services import (
    HithinkDailySyncResult,
    HithinkDailySyncService,
    RecentDataCompletionResult,
    RecentDataCompletionService,
)
from app.services.job_logging import logged_job

logger = logging.getLogger(__name__)


def run_hithink_daily_sync(
    *,
    symbols: list[str],
    days: int = 14,
    adjust_type: str = "none",
    refresh_reference_data: bool = False,
    service: HithinkDailySyncService | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    job_log_repository: JobLogRepository | None = None,
) -> HithinkDailySyncResult:
    with session_factory() as db:
        with logged_job(
            db,
            job_name="hithink_daily_sync",
            job_type="import",
            metadata={
                "symbols_count": len(symbols),
                "days": days,
                "adjust_type": adjust_type,
                "refresh_reference_data": refresh_reference_data,
            },
            repository=job_log_repository,
        ) as job:
            result = (service or HithinkDailySyncService()).sync(
                db,
                symbols=symbols,
                days=days,
                adjust_type=adjust_type,
                refresh_reference_data=refresh_reference_data,
            )
            job.rows_read = result.collection.row_count
            job.rows_inserted = result.market_build.trusted_count
            job.rows_failed = result.collection.rejected_count + result.market_build.rejected_count
            job.metadata_ = {
                **(job.metadata_ or {}),
                "batch_no": result.collection.batch_no,
                "source_name": result.collection.source_name,
                "collection_status": result.collection.status,
                "market_build": {
                    "candidates_count": result.market_build.candidates_count,
                    "trusted_count": result.market_build.trusted_count,
                    "rejected_count": result.market_build.rejected_count,
                    "issue_count": result.market_build.issue_count,
                    "suspect_count": result.market_build.suspect_count,
                    "conflict_count": result.market_build.conflict_count,
                },
            }

    logger.info(
        "HiThink daily sync completed: batch=%s accepted=%s rejected=%s trusted=%s issues=%s",
        result.collection.batch_no,
        result.collection.accepted_count,
        result.collection.rejected_count,
        result.market_build.trusted_count,
        result.market_build.issue_count,
    )
    return result




def run_recent_data_completion(
    *,
    symbols: list[str],
    days: int = 14,
    adjust_type: str = "qfq",
    refresh_reference_data: bool = False,
    service: RecentDataCompletionService | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    job_log_repository: JobLogRepository | None = None,
) -> RecentDataCompletionResult:
    with session_factory() as db:
        with logged_job(
            db,
            job_name="recent_data_completion",
            job_type="import",
            metadata={
                "symbols_count": len(symbols),
                "days": days,
                "adjust_type": adjust_type,
                "refresh_reference_data": refresh_reference_data,
            },
            repository=job_log_repository,
        ) as job:
            result = (service or RecentDataCompletionService()).complete(
                db,
                symbols=symbols,
                days=days,
                adjust_type=adjust_type,
                refresh_reference_data=refresh_reference_data,
            )
            primary = result.primary_sync
            gap = result.gap_backfill
            gap_market = result.gap_market_build
            job.rows_read = primary.collection.row_count + gap.fetched_rows
            job.rows_inserted = primary.market_build.trusted_count + (
                gap_market.trusted_count if gap_market else 0
            )
            job.rows_failed = (
                primary.collection.rejected_count
                + primary.market_build.rejected_count
                + gap.rejected_rows
                + gap.unresolved_actionable_rows
                + (gap_market.rejected_count if gap_market else 0)
                + (gap_market.suspect_count if gap_market else 0)
            )
            if (
                primary.collection.status == "partial"
                or gap.status == "partial"
                or primary.market_build.rejected_count
                or gap.rejected_rows
                or gap.unresolved_rows
                or (gap_market and gap_market.rejected_count)
                or (gap_market and gap_market.suspect_count)
            ):
                job.status = "partial"
            job.metadata_ = {
                **(job.metadata_ or {}),
                "primary_batch_no": primary.collection.batch_no,
                "primary": {
                    "trusted_count": primary.market_build.trusted_count,
                    "rejected_count": primary.market_build.rejected_count,
                    "issue_count": primary.market_build.issue_count,
                },
                "gap_batch_no": gap.batch_no,
                "gap": {
                    "inserted_rows": gap.inserted_rows,
                    "skipped_rows": gap.skipped_rows,
                    "skipped_unchanged_rows": gap.skipped_unchanged_rows,
                    "skipped_duplicate_rows": gap.skipped_duplicate_rows,
                    "ignored_in_scope_rows": gap.ignored_in_scope_rows,
                    "rejected_rows": gap.rejected_rows,
                    "conflicting_response_rows": gap.conflicting_response_rows,
                    "status": gap.status,
                    "source_error_count": gap.source_error_count,
                    "unresolved_rows": gap.unresolved_rows,
                    "unresolved_actionable_rows": gap.unresolved_actionable_rows,
                    "missing_rows": gap.missing_rows,
                    "partial_rows": gap.partial_rows,
                    "suspect_rows": gap.suspect_rows,
                    "status_explained_rows": gap.status_explained_rows,
                    "lifecycle_unknown_rows": gap.lifecycle_unknown_rows,
                    "status_conflict_rows": gap.status_conflict_rows,
                    "trusted_count": gap_market.trusted_count if gap_market else 0,
                    "rejected_count": gap_market.rejected_count if gap_market else 0,
                    "market_suspect_count": gap_market.suspect_count if gap_market else 0,
                    "issue_count": gap_market.issue_count if gap_market else 0,
                },
            }

    logger.info(
        "Recent data completion finished: primary_batch=%s primary_trusted=%s "
        "gap_batch=%s gap_inserted=%s gap_trusted=%s gap_issues=%s",
        result.primary_sync.collection.batch_no,
        result.primary_sync.market_build.trusted_count,
        result.gap_backfill.batch_no,
        result.gap_backfill.inserted_rows,
        result.gap_market_build.trusted_count if result.gap_market_build else 0,
        result.gap_market_build.issue_count if result.gap_market_build else 0,
    )
    return result
