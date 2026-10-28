"""Synchronize the published unadjusted daily dump, retaining raw lineage."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.data_sources.hithink_dump import HithinkDumpKind
from app.repositories.system.auto_data_sync import AUTO_DATA_SYNC_JOB, AutoDataSyncRepository
from app.services.hithink_dump_collection import HithinkDumpCollectionService
from app.services.job_logging import logged_job
from app.services.reference_data import ReferenceDataSyncService

MIN_STOCK_COVERAGE_RATE = 0.95


@dataclass(frozen=True)
class AutoDataSyncResult:
    previous_end_date: date | None
    end_date: date
    advanced: bool
    release_tag: str
    batch_nos: tuple[str, ...]
    rows_read: int
    rows_inserted: int
    market_rows: int
    coverage: dict


def completed_daily_cutoff(now: datetime | None = None) -> date:
    local = (now or datetime.now(ZoneInfo("Asia/Shanghai"))).astimezone(
        ZoneInfo("Asia/Shanghai")
    )
    # Leave time for the vendor's end-of-day publication. Trading-calendar
    # validation in the market builder excludes holidays and weekends.
    return local.date() if local.hour >= 18 else local.date() - timedelta(days=1)


class AutoDataSyncService:
    def __init__(
        self,
        *,
        collection: HithinkDumpCollectionService | None = None,
        reference: ReferenceDataSyncService | None = None,
        repository: AutoDataSyncRepository | None = None,
    ) -> None:
        self.collection = collection or HithinkDumpCollectionService()
        self.reference = reference or ReferenceDataSyncService()
        self.repository = repository or AutoDataSyncRepository()

    def sync(self, db: Session, *, cutoff: date | None = None) -> AutoDataSyncResult:
        cutoff = cutoff or completed_daily_cutoff()
        with logged_job(db, job_name=AUTO_DATA_SYNC_JOB, job_type="import") as job:
            try:
                previous = self.repository.completed_through(db)
                self.reference.sync_hithink_reference_data(db)
                source = self.collection.data_source
                recent = source.download(HithinkDumpKind.RECENT_DAILY)
                recent_start, recent_end = source.daily_date_range(recent.path)
                needs_full = previous is None or self.repository.has_uncovered_sessions(
                    db, after=previous, before=recent_start
                )
                dumps = []
                if needs_full:
                    full = source.download(HithinkDumpKind.FULL_DAILY)
                    full_start, full_end = source.daily_date_range(full.path)
                    if self.repository.has_uncovered_sessions(
                        db, after=full_end, before=recent_start
                    ):
                        raise ValueError("Full and recent dumps do not provide continuous coverage")
                    if previous is not None and self.repository.has_uncovered_sessions(
                        db, after=previous, before=full_start
                    ):
                        raise ValueError("Missing history is older than the available full dump")
                    dumps.append(full)
                dumps.append(recent)
                batches = []
                rows_read = rows_inserted = rows_skipped = market_rows = 0
                ranges = [source.daily_date_range(dump.path) for dump in dumps]
                self.repository.require_calendar_coverage(
                    db,
                    start_date=min(start for start, _ in ranges),
                    end_date=min(max(end for _, end in ranges), cutoff),
                )
                for dump in dumps:
                    result = self.collection.collect(
                        db, full=dump.kind == HithinkDumpKind.FULL_DAILY, dump=dump
                    )
                    market_rows += self.collection.build_market_daily(
                        db, batch_no=result.batch_no, end_date=cutoff
                    )
                    rows_read += result.row_count
                    rows_inserted += result.accepted_count
                    rows_skipped += result.skipped_count
                    batches.append(result.batch_no)
                end_date = min(recent_end, cutoff)
                coverage = self.repository.market_coverage_on_date(
                    db, trade_date=end_date
                )
                if coverage["stock_coverage_rate"] < MIN_STOCK_COVERAGE_RATE:
                    raise ValueError(
                        "Latest stock cross-section is incomplete: "
                        f"{coverage['covered_stocks']}/{coverage['eligible_stocks']} "
                        f"({coverage['stock_coverage_rate']:.4f}) on {end_date}; "
                        f"minimum={MIN_STOCK_COVERAGE_RATE:.4f}"
                    )
                job.rows_read = rows_read
                job.rows_inserted = rows_inserted
                # A successful scan is a processing watermark, not a promise
                # that every security traded or passed the quality gate.
                if previous is not None:
                    end_date = max(previous, end_date)
                job.metadata_ = {
                    "mode": "full_then_recent" if needs_full else "recent",
                    "end_date": end_date.isoformat(),
                    "release_tag": recent.release_tag,
                    "batch_nos": batches,
                    "skipped_rows": rows_skipped,
                    "market_rows": market_rows,
                    "adjust_type": "none",
                    "coverage": coverage,
                }
                result = AutoDataSyncResult(
                    previous_end_date=previous,
                    end_date=end_date,
                    advanced=previous is None or end_date > previous,
                    release_tag=recent.release_tag,
                    batch_nos=tuple(batches),
                    rows_read=rows_read,
                    rows_inserted=rows_inserted,
                    market_rows=market_rows,
                    coverage=coverage,
                )
            except Exception:
                # Recover the transaction before logged_job persists failure.
                db.rollback()
                raise
        return result
