from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.trade import PaperAccount, PaperFill, PaperOrder, PaperPosition
from app.trade import BrokerFill, BrokerOrderRequest


class PaperTradingRepository:
    def get_account(self, db: Session, *, account_id: int) -> PaperAccount | None:
        return db.get(PaperAccount, account_id)

    def get_account_for_update(
        self,
        db: Session,
        *,
        account_id: int,
    ) -> PaperAccount | None:
        return db.scalar(
            select(PaperAccount).where(PaperAccount.id == account_id).with_for_update()
        )

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

    def list_positions(
        self,
        db: Session,
        *,
        account_id: int,
    ) -> list[PaperPosition]:
        return list(
            db.scalars(
                select(PaperPosition)
                .where(
                    PaperPosition.account_id == account_id,
                    PaperPosition.quantity != 0,
                )
                .order_by(PaperPosition.symbol)
            )
        )

    def list_positions_for_update(
        self,
        db: Session,
        *,
        account_id: int,
    ) -> list[PaperPosition]:
        return list(
            db.scalars(
                select(PaperPosition)
                .where(PaperPosition.account_id == account_id)
                .order_by(PaperPosition.symbol)
                .with_for_update()
            )
        )

    def bought_quantity_on_date(
        self,
        db: Session,
        *,
        account_id: int,
        symbol: str,
        trade_date,
    ) -> int:
        return int(
            db.scalar(
                select(func.coalesce(func.sum(PaperFill.quantity), 0)).where(
                    PaperFill.account_id == account_id,
                    PaperFill.symbol == symbol,
                    PaperFill.side == "buy",
                    PaperFill.trade_date == trade_date,
                )
            )
            or 0
        )

    def net_filled_quantity(
        self,
        db: Session,
        *,
        account_id: int,
        symbol: str,
    ) -> int:
        bought = db.scalar(
            select(func.coalesce(func.sum(PaperFill.quantity), 0)).where(
                PaperFill.account_id == account_id,
                PaperFill.symbol == symbol,
                PaperFill.side == "buy",
            )
        )
        sold = db.scalar(
            select(func.coalesce(func.sum(PaperFill.quantity), 0)).where(
                PaperFill.account_id == account_id,
                PaperFill.symbol == symbol,
                PaperFill.side == "sell",
            )
        )
        return int(bought or 0) - int(sold or 0)

    def get_latest_fill_date(
        self,
        db: Session,
        *,
        account_id: int,
    ):
        return db.scalar(
            select(func.max(PaperFill.trade_date)).where(PaperFill.account_id == account_id)
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

    def save_execution_fill(
        self,
        db: Session,
        *,
        account: PaperAccount,
        plan_id: int,
        check_id: int,
        risk_check_order_id: int,
        execution_batch_id: int,
        execution_index: int,
        order: BrokerOrderRequest,
        fill: BrokerFill,
        cash_balance: Decimal,
        position_quantity: int,
        average_cost: Decimal,
    ) -> tuple[PaperOrder, PaperFill]:
        order_record = PaperOrder(
            account_id=account.id,
            plan_id=plan_id,
            check_id=check_id,
            risk_check_order_id=risk_check_order_id,
            execution_batch_id=execution_batch_id,
            execution_index=execution_index,
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
        fill_record = PaperFill(
            order_id=order_record.id,
            account_id=account.id,
            symbol=fill.symbol,
            side=fill.side,
            quantity=fill.quantity,
            price=fill.price,
            commission=fill.commission,
            trade_date=fill.trade_date,
        )
        db.add(fill_record)
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
        db.flush()
        return order_record, fill_record
