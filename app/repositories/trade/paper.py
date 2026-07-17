from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.trade import PaperAccount, PaperFill, PaperOrder, PaperPosition
from app.trade import BrokerFill, BrokerOrderRequest


class PaperTradingRepository:
    def get_account(self, db: Session, *, account_id: int) -> PaperAccount | None:
        return db.get(PaperAccount, account_id)

    def get_position(
        self,
        db: Session,
        *,
        account_id: int,
        symbol: str,
    ) -> PaperPosition | None:
        return db.scalar(
            select(PaperPosition).where(
                PaperPosition.account_id == account_id,
                PaperPosition.symbol == symbol,
            )
        )

    def save_rejected_order(
        self,
        db: Session,
        *,
        account_id: int,
        plan_id: int,
        check_id: int,
        order: BrokerOrderRequest,
        rejection_reason: str,
    ) -> PaperOrder:
        record = PaperOrder(
            account_id=account_id,
            plan_id=plan_id,
            check_id=check_id,
            symbol=order.symbol,
            side=order.side,
            order_quantity=order.quantity,
            reference_price=order.reference_price,
            trade_date=order.trade_date,
            status="rejected",
            rejection_reason=rejection_reason,
        )
        db.add(record)
        return record

    def save_filled_order(
        self,
        db: Session,
        *,
        account: PaperAccount,
        plan_id: int,
        check_id: int,
        order: BrokerOrderRequest,
        fill: BrokerFill,
        cash_balance: Decimal,
        position_quantity: int,
        average_cost: Decimal,
    ) -> PaperOrder:
        order_record = PaperOrder(
            account_id=account.id,
            plan_id=plan_id,
            check_id=check_id,
            symbol=order.symbol,
            side=order.side,
            order_quantity=order.quantity,
            reference_price=order.reference_price,
            trade_date=order.trade_date,
            status="filled",
            rejection_reason=None,
        )
        db.add(order_record)
        db.flush()
        db.add(
            PaperFill(
                order_id=order_record.id,
                account_id=account.id,
                symbol=fill.symbol,
                side=fill.side,
                quantity=fill.quantity,
                price=fill.price,
                commission=fill.commission,
                trade_date=fill.trade_date,
            )
        )
        account.cash_balance = cash_balance
        position = self.get_position(db, account_id=account.id, symbol=order.symbol)
        if position is None:
            position = PaperPosition(
                account_id=account.id,
                symbol=order.symbol,
                quantity=position_quantity,
                average_cost=average_cost,
            )
            db.add(position)
        else:
            position.quantity = position_quantity
            position.average_cost = average_cost
        return order_record
