from datetime import date
from hashlib import sha256
from json import dumps
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fundamental import FinancialStatement, FinancialStatementRevision


def statement_content_hash(row: dict[str, Any]) -> str:
    """Deterministic content hash over the mutable parts of a statement row.

    Covers the fields whose change constitutes a report revision: the
    announcement date (``report_date``), the period end, currency and the
    metrics payload. Identity fields (symbol/statement_type/period/fiscal
    year/fiscal_period) are excluded because they define the report key.
    """

    normalized = {
        "report_date": row["report_date"].isoformat()
        if isinstance(row["report_date"], date)
        else row["report_date"],
        "period_end": row["period_end"].isoformat()
        if isinstance(row["period_end"], date)
        else row["period_end"],
        "currency": row.get("currency") or "CNY",
        "metrics": row.get("metrics") or {},
    }
    return sha256(dumps(normalized, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class FinancialStatementRevisionRepository:
    """Append-only history of every distinct version of a financial statement."""

    def latest_by_key(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
    ) -> dict[tuple[str, str, str, int, str], FinancialStatementRevision]:
        """Return the highest ``revision_no`` row per statement key.

        The statement key is (symbol, statement_type, period, fiscal_year,
        fiscal_period). Consumers use this to bind a factor run to the exact
        statement content that was current when the run executed.
        """

        filters = []
        if symbols:
            filters.append(FinancialStatementRevision.symbol.in_(symbols))
        rows = db.scalars(
            select(FinancialStatementRevision)
            .where(*filters)
            .order_by(
                FinancialStatementRevision.symbol,
                FinancialStatementRevision.statement_type,
                FinancialStatementRevision.period,
                FinancialStatementRevision.fiscal_year,
                FinancialStatementRevision.fiscal_period,
                FinancialStatementRevision.revision_no,
            )
        ).all()
        latest: dict[tuple[str, str, str, int, str], FinancialStatementRevision] = {}
        for row in rows:
            key = (
                row.symbol,
                row.statement_type,
                row.period,
                row.fiscal_year,
                row.fiscal_period,
            )
            latest[key] = row
        return latest

    def list_for_statement(
        self,
        db: Session,
        *,
        symbol: str,
        statement_type: str,
        period: str,
        fiscal_year: int,
        fiscal_period: str,
    ) -> list[FinancialStatementRevision]:
        return list(
            db.scalars(
                select(FinancialStatementRevision)
                .where(
                    FinancialStatementRevision.symbol == symbol,
                    FinancialStatementRevision.statement_type == statement_type,
                    FinancialStatementRevision.period == period,
                    FinancialStatementRevision.fiscal_year == fiscal_year,
                    FinancialStatementRevision.fiscal_period == fiscal_period,
                )
                .order_by(FinancialStatementRevision.revision_no)
            ).all()
        )

    def append(
        self,
        db: Session,
        *,
        row: dict[str, Any],
        revision_no: int,
        row_hash: str,
        batch_id: int | None = None,
    ) -> FinancialStatementRevision:
        revision = FinancialStatementRevision(
            symbol=row["symbol"],
            statement_type=row["statement_type"],
            period=row["period"],
            fiscal_year=row["fiscal_year"],
            fiscal_period=row["fiscal_period"],
            revision_no=revision_no,
            report_date=row["report_date"],
            period_end=row["period_end"],
            currency=row.get("currency") or "CNY",
            metrics=row.get("metrics") or {},
            source_name=row["source_name"],
            row_hash=row_hash,
            batch_id=batch_id,
        )
        db.add(revision)
        db.flush()
        return revision

    def next_revision_no(
        self,
        db: Session,
        *,
        symbol: str,
        statement_type: str,
        period: str,
        fiscal_year: int,
        fiscal_period: str,
    ) -> int:
        current = db.scalar(
            select(FinancialStatementRevision.revision_no)
            .where(
                FinancialStatementRevision.symbol == symbol,
                FinancialStatementRevision.statement_type == statement_type,
                FinancialStatementRevision.period == period,
                FinancialStatementRevision.fiscal_year == fiscal_year,
                FinancialStatementRevision.fiscal_period == fiscal_period,
            )
            .order_by(FinancialStatementRevision.revision_no.desc())
            .limit(1)
        )
        return (current or 0) + 1

    def current_statement(
        self,
        db: Session,
        *,
        symbol: str,
        statement_type: str,
        period: str,
        fiscal_year: int,
        fiscal_period: str,
    ) -> FinancialStatement | None:
        return db.scalar(
            select(FinancialStatement).where(
                FinancialStatement.symbol == symbol,
                FinancialStatement.statement_type == statement_type,
                FinancialStatement.period == period,
                FinancialStatement.fiscal_year == fiscal_year,
                FinancialStatement.fiscal_period == fiscal_period,
            )
        )
