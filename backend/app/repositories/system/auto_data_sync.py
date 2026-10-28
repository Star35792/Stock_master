from datetime import date, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.market import DailyBar, Security, TradingCalendar
from app.models.system import JobLog

AUTO_DATA_SYNC_JOB = "auto_data_sync"


class AutoDataSyncRepository:
    def market_coverage_on_date(self, db: Session, *, trade_date: date) -> dict:
        """Return stock coverage by exchange plus separately reported index rows."""
        eligibility = (
            Security.security_type == "stock",
            Security.is_active.is_(True),
            or_(Security.list_date.is_(None), Security.list_date <= trade_date),
            or_(Security.delist_date.is_(None), Security.delist_date >= trade_date),
        )
        eligible_rows = db.execute(
            select(Security.exchange, func.count())
            .where(*eligibility)
            .group_by(Security.exchange)
        ).all()
        covered_rows = db.execute(
            select(Security.exchange, func.count(func.distinct(DailyBar.symbol)))
            .join(DailyBar, DailyBar.symbol == Security.symbol)
            .where(
                *eligibility,
                DailyBar.trade_date == trade_date,
                DailyBar.adjust_type == "none",
                DailyBar.quality_status.in_(("trusted", "manual_fixed")),
                DailyBar.is_suspect.is_(False),
            )
            .group_by(Security.exchange)
        ).all()
        eligible = {str(exchange): int(count) for exchange, count in eligible_rows}
        covered = {str(exchange): int(count) for exchange, count in covered_rows}
        eligible_total = sum(eligible.values())
        covered_total = sum(covered.values())
        index_rows = int(
            db.scalar(
                select(func.count(func.distinct(DailyBar.symbol)))
                .join(Security, Security.symbol == DailyBar.symbol)
                .where(
                    Security.security_type == "index",
                    DailyBar.trade_date == trade_date,
                    DailyBar.adjust_type == "none",
                    DailyBar.quality_status.in_(("trusted", "manual_fixed")),
                    DailyBar.is_suspect.is_(False),
                )
            )
            or 0
        )
        return {
            "trade_date": trade_date.isoformat(),
            "eligible_stocks": eligible_total,
            "covered_stocks": covered_total,
            "stock_coverage_rate": (
                covered_total / eligible_total if eligible_total else 0.0
            ),
            "by_exchange": {
                exchange: {
                    "eligible": count,
                    "covered": covered.get(exchange, 0),
                }
                for exchange, count in sorted(eligible.items())
            },
            "index_rows": index_rows,
        }

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
            # Sequence ids can be rebuilt after control-plane compaction.  They
            # are therefore not a reliable recency signal; use the actual job
            # timestamp and keep id only as a deterministic tie-breaker.
            .order_by(JobLog.started_at.desc(), JobLog.id.desc())
            .limit(1)
        )
        value = (job.metadata_ or {}).get("end_date") if job else None
        return date.fromisoformat(value) if value else None
