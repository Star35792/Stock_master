from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.trade import PaperExecutionApproval, PaperExecutionBatch


class PaperExecutionApprovalRepository:
    def get(self, db: Session, *, approval_id: int) -> PaperExecutionApproval | None:
        return db.get(PaperExecutionApproval, approval_id)

    def get_for_update(
        self,
        db: Session,
        *,
        approval_id: int,
    ) -> PaperExecutionApproval | None:
        return db.scalar(
            select(PaperExecutionApproval).where(PaperExecutionApproval.id == approval_id)
        )

    def get_by_idempotency_key(
        self,
        db: Session,
        *,
        idempotency_key: str,
    ) -> PaperExecutionApproval | None:
        return db.scalar(
            select(PaperExecutionApproval).where(
                PaperExecutionApproval.idempotency_key == idempotency_key
            )
        )

    def get_by_risk_check_id(
        self,
        db: Session,
        *,
        risk_check_id: int,
    ) -> PaperExecutionApproval | None:
        return db.scalar(
            select(PaperExecutionApproval).where(
                PaperExecutionApproval.risk_check_id == risk_check_id
            )
        )

    def save(self, db: Session, *, values: dict) -> PaperExecutionApproval:
        approval = PaperExecutionApproval(**values)
        db.add(approval)
        db.flush()
        return approval


class PaperExecutionBatchRepository:
    def get(self, db: Session, *, execution_id: int) -> PaperExecutionBatch | None:
        return db.get(PaperExecutionBatch, execution_id)

    def get_by_idempotency_key(
        self,
        db: Session,
        *,
        idempotency_key: str,
    ) -> PaperExecutionBatch | None:
        return db.scalar(
            select(PaperExecutionBatch).where(
                PaperExecutionBatch.idempotency_key == idempotency_key
            )
        )

    def get_by_approval_id(
        self,
        db: Session,
        *,
        approval_id: int,
    ) -> PaperExecutionBatch | None:
        return db.scalar(
            select(PaperExecutionBatch).where(PaperExecutionBatch.approval_id == approval_id)
        )

    def save(self, db: Session, *, values: dict) -> PaperExecutionBatch:
        batch = PaperExecutionBatch(**values)
        db.add(batch)
        db.flush()
        return batch
