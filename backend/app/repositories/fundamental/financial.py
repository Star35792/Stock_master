from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fundamental import FinancialStatement


class FinancialStatementRepository:
    def list_available(
        self,
        db: Session,
        *,
        end_date: date,
        symbols: list[str] | None = None,
    ) -> list[FinancialStatement]:
        filters = [FinancialStatement.report_date <= end_date]
        if symbols:
            filters.append(FinancialStatement.symbol.in_(symbols))

        return list(
            db.scalars(
                select(FinancialStatement)
                .where(*filters)
                .order_by(
                    FinancialStatement.symbol,
                    FinancialStatement.report_date,
                    FinancialStatement.period_end,
                    FinancialStatement.statement_type,
                )
            )
        )
