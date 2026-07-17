from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.raw import ImportBatch, RawDailyBar


class RawDailyBarImportRepository:
    def create_batch(
        self,
        db: Session,
        *,
        batch_no: str,
        source_name: str,
        source_type: str,
        dataset: str,
        request_params: dict,
    ) -> ImportBatch:
        batch = ImportBatch(
            batch_no=batch_no,
            source_name=source_name,
            source_type=source_type,
            dataset=dataset,
            request_params=request_params,
            status="running",
        )
        db.add(batch)
        db.flush()
        return batch

    def add_daily_bar(self, db: Session, row: RawDailyBar) -> None:
        db.add(row)

    def finish_batch(
        self,
        db: Session,
        batch: ImportBatch,
        *,
        status: str,
        row_count: int,
        accepted_count: int,
        rejected_count: int,
        error_message: str | None = None,
    ) -> None:
        batch.status = status
        batch.row_count = row_count
        batch.accepted_count = accepted_count
        batch.rejected_count = rejected_count
        batch.error_message = error_message
        batch.finished_at = db.scalar(select(func.clock_timestamp()))
