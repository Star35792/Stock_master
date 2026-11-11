"""Bulk reads for the read-only price/value research screen."""

from datetime import date, timedelta

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from app.models.factor import FactorRun, FactorValue
from app.models.fundamental import FinancialStatement
from app.models.market import (
    AdjustmentFactor,
    DailyBar,
    Security,
    TradingCalendar,
    ValuationSnapshot,
)
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.models.strategy import BacktestRun
from app.models.system import JobLog
from app.models.trade import PaperAccount, PaperAccountEquitySnapshot


class PriceValueScreenRepository:
    def freshness_calendar(self, db: Session, *, today: date) -> list[dict]:
        return [dict(row._mapping) for row in db.execute(
            select(TradingCalendar.exchange, TradingCalendar.trade_date, TradingCalendar.is_open)
            .where(TradingCalendar.trade_date.between(today - timedelta(days=32), today))
        )]

    def diagnostic_inputs(
        self, db: Session, *, trade_date: date, financial_as_of: date | None = None,
    ) -> dict:
        financial_date = financial_as_of or trade_date
        sessions = {}
        for exchange in ("SH", "SZ", "BJ"):
            sessions[exchange] = sorted(db.scalars(
                select(TradingCalendar.trade_date).where(
                    TradingCalendar.exchange == exchange,
                    TradingCalendar.trade_date <= trade_date,
                    TradingCalendar.is_open.is_(True),
                ).order_by(TradingCalendar.trade_date.desc()).limit(21)
            ))
        start = min((days[0] for days in sessions.values() if days), default=trade_date)
        calendar = {(r.exchange, r.trade_date): r.is_open for r in db.execute(
            select(TradingCalendar.exchange, TradingCalendar.trade_date, TradingCalendar.is_open)
            .where(TradingCalendar.trade_date.between(start, trade_date))
        )}
        for exchange, days in sessions.items():
            if not days or any(
                len({calendar.get((venue, days[0] + timedelta(days=offset)))
                     for venue in ("SH", "SZ", "BJ")}) != 1
                or calendar.get((exchange, days[0] + timedelta(days=offset))) is None
                for offset in range((trade_date - days[0]).days + 1)
            ):
                sessions[exchange] = []
        price_columns = (
            DailyBar.id, DailyBar.symbol, DailyBar.trade_date, DailyBar.close, DailyBar.amount,
            DailyBar.adjust_type, DailyBar.source_name, DailyBar.quality_status,
            DailyBar.is_suspect, DailyBar.is_suspended, DailyBar.updated_at,
        )
        prices = [dict(row._mapping) for row in db.execute(select(*price_columns).where(
            DailyBar.trade_date.between(start, trade_date),
            DailyBar.adjust_type.in_(("none", "qfq")), DailyBar.security_type == "stock",
        ))]
        factors = [dict(row._mapping) for row in db.execute(select(
            AdjustmentFactor.id, AdjustmentFactor.symbol, AdjustmentFactor.trade_date,
            AdjustmentFactor.factor, AdjustmentFactor.source_name, AdjustmentFactor.version,
            AdjustmentFactor.release_id, AdjustmentFactor.quality_status,
            AdjustmentFactor.updated_at,
        ).where(AdjustmentFactor.trade_date.between(start, trade_date),
                AdjustmentFactor.adjust_type == "qfq"))]
        financial = [dict(row._mapping) for row in db.execute(select(
            FinancialStatement.id, FinancialStatement.symbol, FinancialStatement.statement_type,
            FinancialStatement.period_end, FinancialStatement.report_date,
            FinancialStatement.currency, FinancialStatement.source_name,
            # Preserve JSON decimal tokens: the ORM JSON loader creates binary
            # floats, which the exact financial evidence contract rejects.
            cast(FinancialStatement.metrics, String).label("metrics"),
            FinancialStatement.created_at,
            FinancialStatement.updated_at,
        ).where(
            FinancialStatement.report_date <= financial_date,
            FinancialStatement.period_end.between(
                date(financial_date.year - 3, 1, 1), financial_date,
            ),
            FinancialStatement.currency == "CNY",
            FinancialStatement.statement_type.in_(("income", "cash_flow")),
        ))]
        return {"sessions": sessions, "prices": prices, "adjustments": factors,
                "financial_rows": financial}

    def validated_raw_hashes(
        self, db: Session, *, batch_id: int,
        dataset: str = "price_value_valuation_observation",
    ) -> set[str] | None:
        batch = db.get(ImportBatch, batch_id)
        if batch is None or batch.status != "success":
            return None
        return set(
            db.scalars(
                select(RawVendorSnapshot.row_hash).where(
                    RawVendorSnapshot.batch_id == batch_id,
                    RawVendorSnapshot.dataset == dataset,
                )
            )
        )

    def completed_daily_sync(self, db: Session, *, trade_date: date) -> JobLog | None:
        for job in db.scalars(
            select(JobLog)
            .where(JobLog.job_name == "auto_data_sync", JobLog.status == "success")
            .order_by(JobLog.started_at.desc(), JobLog.id.desc())
            .limit(20)
        ):
            if (job.metadata_ or {}).get("end_date", "") >= trade_date.isoformat():
                return job
        return None

    def latest_daily_sync_job(self, db: Session) -> JobLog | None:
        return db.scalar(
            select(JobLog).where(JobLog.job_name == "auto_data_sync")
            .order_by(JobLog.started_at.desc(), JobLog.id.desc()).limit(1)
        )

    def latest_historical_archive(self, db: Session, *, trade_date: date) -> JobLog | None:
        for job in db.scalars(
            select(JobLog).where(
                JobLog.job_name == "price_value_historical_valuation_collect",
                JobLog.status == "success",
            ).order_by(JobLog.started_at.desc(), JobLog.id.desc()).limit(20)
        ):
            if (job.metadata_ or {}).get("target_trade_date") == trade_date.isoformat():
                return job
        return None

    def completed_input_prep(self, db: Session, *, trade_date: date) -> JobLog | None:
        return next(
            (
                job
                for job in db.scalars(
                    select(JobLog)
                    .where(
                        JobLog.job_name == "price_value_input_prep",
                        JobLog.status == "success",
                    )
                    .order_by(JobLog.started_at.desc(), JobLog.id.desc())
                    .limit(20)
                )
                if (job.metadata_ or {}).get("target_trade_date") == trade_date.isoformat()
            ),
            None,
        )

    def latest_open_date(self, db: Session, *, before: date) -> date | None:
        return db.scalar(
            select(TradingCalendar.trade_date)
            .where(
                TradingCalendar.exchange == "SH",
                TradingCalendar.trade_date < before,
                TradingCalendar.is_open.is_(True),
            )
            .order_by(TradingCalendar.trade_date.desc())
            .limit(1)
        )

    def calendar_exchanges(self, db: Session, *, trade_date: date) -> set[str]:
        return set(
            db.scalars(
                select(TradingCalendar.exchange).where(
                    TradingCalendar.trade_date == trade_date,
                    TradingCalendar.is_open.is_(True),
                )
            )
        )

    def stocks(self, db: Session) -> list[Security]:
        return list(
            db.scalars(
                select(Security)
                .where(Security.security_type == "stock")
                .order_by(Security.symbol)
            )
        )

    def bars(self, db: Session, *, trade_date: date) -> dict[str, DailyBar]:
        return {
            row.symbol: row
            for row in db.scalars(
                select(DailyBar).where(
                    DailyBar.trade_date == trade_date,
                    DailyBar.adjust_type == "none",
                    DailyBar.security_type == "stock",
                )
            )
        }

    def valuation(self, db: Session, *, trade_date: date) -> dict[str, ValuationSnapshot]:
        # The existing HiThink snapshots have no PE/PB. Never mix suppliers or dates.
        return {
            row.symbol: row
            for row in db.scalars(
                select(ValuationSnapshot).where(
                    ValuationSnapshot.trade_date == trade_date,
                    ValuationSnapshot.source_name == "eastmoney",
                )
            )
        }

    def roe_proxy(self, db: Session, *, trade_date: date) -> dict[str, FactorValue]:
        successful_runs = set(
            db.scalars(
                select(FactorRun.id).where(
                    FactorRun.factor_name == "roe_ttm",
                    FactorRun.factor_version == "v1",
                    FactorRun.status == "success",
                )
            )
        )
        return {
            row.symbol: row
            for row in db.scalars(
                select(FactorValue).where(
                    FactorValue.trade_date == trade_date,
                    FactorValue.factor_name == "roe_ttm",
                    FactorValue.factor_version == "v1",
                )
            )
            if row.run_id in successful_runs
        }

    def latest_screen_job(self, db: Session) -> JobLog | None:
        return db.scalar(
            select(JobLog)
            .where(JobLog.job_name == "price_value_screen")
            .order_by(JobLog.started_at.desc(), JobLog.id.desc())
            .limit(1)
        )

    def recent_screen_jobs(self, db: Session, *, limit: int = 20) -> list[JobLog]:
        return list(
            db.scalars(
                select(JobLog)
                .where(JobLog.job_name == "price_value_screen")
                .order_by(JobLog.started_at.desc(), JobLog.id.desc())
                .limit(limit)
            )
        )

    def latest_price_value_backtest(self, db: Session) -> BacktestRun | None:
        return db.scalar(
            select(BacktestRun)
            .where(
                BacktestRun.strategy_name == "price_value_assumption_v1",
                BacktestRun.status == "success",
            )
            .order_by(BacktestRun.finished_at.desc(), BacktestRun.id.desc())
            .limit(1)
        )

    def paper_accounts(self, db: Session) -> list[PaperAccount]:
        return list(db.scalars(select(PaperAccount).order_by(PaperAccount.id)))

    def latest_paper_snapshot(
        self, db: Session, *, account_id: int
    ) -> PaperAccountEquitySnapshot | None:
        return db.scalar(
            select(PaperAccountEquitySnapshot)
            .where(PaperAccountEquitySnapshot.account_id == account_id)
            .order_by(
                PaperAccountEquitySnapshot.valuation_date.desc(),
                PaperAccountEquitySnapshot.id.desc(),
            )
            .limit(1)
        )
