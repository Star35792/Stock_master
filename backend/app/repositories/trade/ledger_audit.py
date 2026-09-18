from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.trade import (
    PaperAccount,
    PaperExecutionBatch,
    PaperFill,
    PaperOrder,
    PaperPosition,
)


@dataclass(frozen=True)
class PaperLedgerAuditFacts:
    account: PaperAccount
    orders: list[PaperOrder]
    fills: list[PaperFill]
    positions: list[PaperPosition]
    completed_batches: list[PaperExecutionBatch]


class PaperLedgerAuditRepository:
    def get_facts(
        self,
        db: Session,
        *,
        account_id: int,
    ) -> PaperLedgerAuditFacts | None:
        account = db.get(PaperAccount, account_id)
        if account is None:
            return None
        return PaperLedgerAuditFacts(
            account=account,
            orders=list(
                db.scalars(
                    select(PaperOrder)
                    .where(PaperOrder.account_id == account_id)
                    .order_by(PaperOrder.trade_date, PaperOrder.id)
                )
            ),
            fills=list(
                db.scalars(
                    select(PaperFill)
                    .where(PaperFill.account_id == account_id)
                    .order_by(PaperFill.trade_date, PaperFill.id)
                )
            ),
            positions=list(
                db.scalars(
                    select(PaperPosition)
                    .where(PaperPosition.account_id == account_id)
                    .order_by(PaperPosition.symbol)
                )
            ),
            completed_batches=list(
                db.scalars(
                    select(PaperExecutionBatch)
                    .where(
                        PaperExecutionBatch.account_id == account_id,
                        PaperExecutionBatch.status == "completed",
                    )
                    .order_by(PaperExecutionBatch.completed_at, PaperExecutionBatch.id)
                )
            ),
        )
