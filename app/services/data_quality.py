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
