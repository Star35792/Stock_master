"""Read-only availability checks for an operator-requested factor rerun."""

from datetime import date

import duckdb
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.lake.curated_reader import CuratedDailyBarReader
from app.models.factor import FactorDefinition, FactorValue
from app.models.fundamental import FinancialStatement
from app.models.market import (
    AlternativeDataSnapshot,
    DailyBar,
    Security,
    TradingCalendar,
    ValuationSnapshot,
)
from app.repositories.market import DailyBarRepository


class FactorRecoveryRepository:
    def applicable_symbols(self, db: Session, *, symbols: list[str],
                           security_types: tuple[str, ...]) -> tuple[set[str], set[str]]:
        rows = db.execute(select(Security.symbol, Security.security_type).where(
            Security.symbol.in_(symbols),
        )).all()
        known = {symbol for symbol, _kind in rows}
        applicable = {symbol for symbol, kind in rows if kind in security_types}
        return applicable, set(symbols) - known

    def target_dates(
        self, db: Session, *, start_date: date, end_date: date, symbols: list[str] | None
    ) -> set[date]:
        exchanges = (
            {symbol.rsplit(".", 1)[-1] for symbol in symbols}
            if symbols
            else {
                "SH",
                "SZ",
                "BJ",
            }
        )
        return set(
            db.scalars(
                select(TradingCalendar.trade_date)
                .where(
                    TradingCalendar.exchange.in_(exchanges),
                    TradingCalendar.is_open.is_(True),
                    TradingCalendar.trade_date.between(start_date, end_date),
                )
                .distinct()
            )
        )

    def profile_input_summary(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        categories: list[str] | None,
        excluded_categories: list[str] | None,
    ) -> dict:
        filters = [
            FactorDefinition.is_active.is_(True),
            ~FactorDefinition.name.contains("__", autoescape=True),
        ]
        if categories:
            filters.append(FactorDefinition.category.in_(categories))
        if excluded_categories:
            filters.append(~FactorDefinition.category.in_(excluded_categories))
        definitions = list(db.scalars(select(FactorDefinition).where(*filters)))
        available = set(
            db.execute(
                select(
                    FactorValue.factor_name,
                    FactorValue.factor_version,
                )
                .where(FactorValue.trade_date.between(start_date, end_date))
                .distinct()
            ).all()
        )
        missing = [
            f"{item.name}@{item.version}"
            for item in definitions
            if (item.name, item.version) not in available
        ]
        return {
            "candidate_count": len(definitions),
            "missing_count": len(missing),
            "available_count": len(definitions) - len(missing),
            "missing_factor_examples": missing[:20],
        }

    def price_counts(
        self,
        db: Session,
        *,
        reader,
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbols: list[str] | None,
        security_types: tuple[str, ...],
    ) -> dict[date, int]:
        if isinstance(reader, CuratedDailyBarReader):
            filters = [
                "trade_date BETWEEN ? AND ?",
                f"security_type IN ({','.join('?' for _ in security_types)})",
            ]
            params = [reader._glob(), start_date, end_date, *security_types]
            if symbols:
                filters.append(f"symbol IN ({','.join('?' for _ in symbols)})")
                params.extend(symbols)
            connection = duckdb.connect()
            try:
                return dict(
                    connection.execute(
                        "SELECT trade_date, count(*) FROM read_parquet(?, hive_partitioning=true) "
                        f"WHERE {' AND '.join(filters)} GROUP BY trade_date",
                        params,
                    ).fetchall()
                )
            finally:
                connection.close()
        filters = [
            DailyBar.trade_date.between(start_date, end_date),
            DailyBar.adjust_type == adjust_type,
            DailyBar.quality_status == "trusted",
            DailyBar.is_suspect.is_(False),
            DailyBar.security_type.in_(security_types),
        ]
        if symbols:
            filters.append(DailyBar.symbol.in_(symbols))
        return dict(
            db.execute(
                select(DailyBar.trade_date, func.count())
                .where(
                    *filters,
                )
                .group_by(DailyBar.trade_date)
            ).all()
        )

    def dependency_counts(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        symbols: list[str] | None,
        financial: bool = False,
        valuation: bool = False,
        datasets: tuple[str, ...] = (),
        components: tuple[tuple[str, str], ...] = (),
    ) -> dict[str, int]:
        result: dict[str, int] = {}
        if financial:
            query = (
                select(func.count())
                .select_from(FinancialStatement)
                .where(
                    FinancialStatement.report_date < end_date,
                )
            )
            if symbols:
                query = query.where(FinancialStatement.symbol.in_(symbols))
            result["financial_statements"] = db.scalar(query) or 0
        if valuation:
            query = (
                select(func.count())
                .select_from(ValuationSnapshot)
                .where(
                    ValuationSnapshot.trade_date.between(start_date, end_date),
                    ValuationSnapshot.raw_payload["_research_batch_id"].as_string().is_(None),
                )
            )
            if symbols:
                query = query.where(ValuationSnapshot.symbol.in_(symbols))
            result["generic_valuations"] = db.scalar(query) or 0
        for dataset in datasets:
            result[dataset] = (
                db.scalar(
                    select(func.count())
                    .select_from(AlternativeDataSnapshot)
                    .where(
                        AlternativeDataSnapshot.dataset == dataset,
                        AlternativeDataSnapshot.snapshot_date >= start_date,
                        AlternativeDataSnapshot.snapshot_date < end_date,
                    )
                )
                or 0
            )
        for name, version in components:
            result[f"{name}@{version}"] = (
                db.scalar(
                    select(func.count())
                    .select_from(FactorValue)
                    .where(
                        FactorValue.factor_name == name,
                        FactorValue.factor_version == version,
                        FactorValue.trade_date.between(start_date, end_date),
                    )
                )
                or 0
            )
        return result

    def symbol_price_counts(
        self,
        db: Session,
        *,
        reader,
        start_date: date,
        target_start_date: date,
        end_date: date,
        adjust_type: str,
        symbols: list[str] | None,
        security_types: tuple[str, ...],
    ) -> dict[str, tuple[int, int]]:
        if isinstance(reader, CuratedDailyBarReader):
            filters = [
                "trade_date BETWEEN ? AND ?",
                f"security_type IN ({','.join('?' for _ in security_types)})",
            ]
            params = [target_start_date, reader._glob(), start_date, end_date, *security_types]
            if symbols:
                filters.append(f"symbol IN ({','.join('?' for _ in symbols)})")
                params.extend(symbols)
            connection = duckdb.connect()
            try:
                rows = connection.execute(
                    "SELECT symbol, count(*), count(*) FILTER (WHERE trade_date >= ?) "
                    "FROM read_parquet(?, hive_partitioning=true) "
                    f"WHERE {' AND '.join(filters)} GROUP BY symbol",
                    params,
                ).fetchall()
            finally:
                connection.close()
        else:
            filters = [
                DailyBar.trade_date.between(start_date, end_date),
                DailyBar.adjust_type == adjust_type,
                DailyBar.quality_status == "trusted",
                DailyBar.is_suspect.is_(False),
                DailyBar.security_type.in_(security_types),
            ]
            if symbols:
                filters.append(DailyBar.symbol.in_(symbols))
            rows = db.execute(
                select(
                    DailyBar.symbol,
                    func.count(),
                    func.count().filter(DailyBar.trade_date >= target_start_date),
                )
                .where(*filters)
                .group_by(DailyBar.symbol)
            ).all()
        return {symbol: (total, target) for symbol, total, target in rows}

    def output_summary(self, db: Session, *, run_id: int) -> dict:
        count, dates, symbols, first, last = db.execute(
            select(
                func.count(),
                func.count(func.distinct(FactorValue.trade_date)),
                func.count(func.distinct(FactorValue.symbol)),
                func.min(FactorValue.trade_date),
                func.max(FactorValue.trade_date),
            ).where(FactorValue.run_id == run_id, FactorValue.value.is_not(None))
        ).one()
        return {
            "rows": count,
            "trade_dates": dates,
            "symbols": symbols,
            "first_trade_date": first.isoformat() if first else None,
            "last_trade_date": last.isoformat() if last else None,
        }

    def output_symbol_counts(self, db: Session, *, run_id: int) -> dict[str, int]:
        return dict(
            db.execute(
                select(FactorValue.symbol, func.count())
                .where(
                    FactorValue.run_id == run_id,
                    FactorValue.value.is_not(None),
                )
                .group_by(FactorValue.symbol)
            ).all()
        )

    def control_reader(self) -> DailyBarRepository:
        return DailyBarRepository()
