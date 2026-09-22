from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import TradingCalendar
from app.models.system import JobLog

AUTO_DATA_SYNC_JOB = "auto_data_sync"


class AutoDataSyncRepository:
    def require_calendar_coverage(self, db: Session, *, start_date: date, end_date: date) -> None:
        """Require each mainland exchange calendar to bracket the scan window."""
        if start_date > end_date:
            return
        exchanges = ("SH", "SZ", "BJ")
        coverage = db.execute(
            select(
                TradingCalendar.exchange,
                func.min(TradingCalendar.trade_date),
                func.max(TradingCalendar.trade_date),
            )
            .where(TradingCalendar.exchange.in_(exchanges))
            .group_by(TradingCalendar.exchange)
        ).all()
        by_exchange = {exchange: (first, last) for exchange, first, last in coverage}
        missing = [
            exchange
            for exchange in exchanges
            if exchange not in by_exchange
            or by_exchange[exchange][0] > start_date
            or by_exchange[exchange][1] < end_date
        ]
        if missing:
            raise ValueError(
                f"Trading calendar coverage is incomplete for {', '.join(missing)} "
                f"between {start_date} and {end_date}"
            )

    def has_uncovered_sessions(self, db: Session, *, after: date, before: date) -> bool:
        """Treat only calendar-proven closed intervals as covered by adjacent dumps."""
        start = after + timedelta(days=1)
        end = before - timedelta(days=1)
        if start > end:
            return False
        try:
            self.require_calendar_coverage(db, start_date=start, end_date=end)
        except ValueError:
            return True
        return bool(
            db.scalar(
                select(TradingCalendar.id)
                .where(
                    TradingCalendar.exchange.in_(("SH", "SZ", "BJ")),
                    TradingCalendar.trade_date.between(start, end),
                    TradingCalendar.is_open.is_(True),
                )
                .limit(1)
            )
        )

    def completed_through(self, db: Session) -> date | None:
        job = db.scalar(
            select(JobLog)
            .where(JobLog.job_name == AUTO_DATA_SYNC_JOB, JobLog.status == "success")
            .order_by(JobLog.id.desc())
            .limit(1)
        )
        value = (job.metadata_ or {}).get("end_date") if job else None
        return date.fromisoformat(value) if value else None
