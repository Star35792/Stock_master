from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.system import DataQualityIssue


class DataQualityIssueRepository:
    def create(
        self,
        db: Session,
        *,
        dataset: str,
        issue_type: str,
        severity: str,
        description: str,
        symbol: str | None = None,
        trade_date: date | None = None,
        adjust_type: str | None = None,
        source_name: str | None = None,
        raw_id: int | None = None,
        expected_value: dict[str, Any] | None = None,
        actual_value: dict[str, Any] | None = None,
    ) -> DataQualityIssue:
        issue = DataQualityIssue(
            dataset=dataset,
            symbol=symbol,
            trade_date=trade_date,
            adjust_type=adjust_type,
            source_name=source_name,
            raw_id=raw_id,
            issue_type=issue_type,
            severity=severity,
            description=description,
            expected_value=expected_value,
            actual_value=actual_value,
            status="open",
        )
        db.add(issue)
        return issue

    def list(
        self,
        db: Session,
        *,
        status: str | None = "open",
        severity: str | None = None,
        issue_type: str | None = None,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[DataQualityIssue], int]:
        filters = []
        if status:
            filters.append(DataQualityIssue.status == status)
        if severity:
            filters.append(DataQualityIssue.severity == severity)
        if issue_type:
            filters.append(DataQualityIssue.issue_type == issue_type)
        if symbol:
            filters.append(DataQualityIssue.symbol == symbol)
        if start_date:
            filters.append(DataQualityIssue.trade_date >= start_date)
        if end_date:
            filters.append(DataQualityIssue.trade_date <= end_date)

        total = db.scalar(select(func.count()).select_from(DataQualityIssue).where(*filters)) or 0
        items = list(
            db.scalars(
                select(DataQualityIssue)
                .where(*filters)
                .order_by(DataQualityIssue.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total
