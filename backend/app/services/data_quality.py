from datetime import date

from sqlalchemy.orm import Session

from app.repositories.system import DataQualityIssueRepository
from app.schemas import DataQualityIssueRead, Page


class DataQualityQueryService:
    def __init__(self, repository: DataQualityIssueRepository | None = None) -> None:
        self.repository = repository or DataQualityIssueRepository()

    def list_issues(
        self,
        db: Session,
        *,
        status: str | None,
        severity: str | None,
        issue_type: str | None,
        symbol: str | None,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
    ) -> Page[DataQualityIssueRead]:
        rows, total = self.repository.list(
            db,
            status=status,
            severity=severity,
            issue_type=issue_type,
            symbol=symbol.upper() if symbol else None,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[DataQualityIssueRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def update_issue_status(
        self,
        db: Session,
        *,
        issue_id: int,
        status: str,
        resolved_by: str | None,
    ) -> DataQualityIssueRead | None:
        issue = self.repository.update_status(
            db,
            issue_id=issue_id,
            status=status,
            resolved_by=resolved_by,
        )
        if issue is None:
            return None
        db.commit()
        db.refresh(issue)
        return DataQualityIssueRead.model_validate(issue)
