from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.repositories.portfolio import PortfolioPlanRepository
from app.repositories.risk import RiskCheckRepository
from app.repositories.trade import PaperTradingRepository
from app.trade import BrokerAdapter, BrokerFill, BrokerOrderRequest, PaperBrokerAdapter


@dataclass(frozen=True)
class PaperTradingResult:
    account_id: int
    check_id: int
    order_count: int
    fill_count: int
    rejected_count: int
    cash_balance: Decimal


class PaperTradingService:
    def __init__(
        self,
        paper_repository: PaperTradingRepository | None = None,
        risk_repository: RiskCheckRepository | None = None,
        plan_repository: PortfolioPlanRepository | None = None,
        broker: BrokerAdapter | None = None,
    ) -> None:
        self.paper_repository = paper_repository or PaperTradingRepository()
        self.risk_repository = risk_repository or RiskCheckRepository()
        self.plan_repository = plan_repository or PortfolioPlanRepository()
        self.broker = broker or PaperBrokerAdapter()

    def execute_check(
        self,
        db: Session,
        *,
        account_id: int,
        check_id: int,
    ) -> PaperTradingResult:
        account = self.paper_repository.get_account(db, account_id=account_id)
        if account is None:
            raise ValueError("paper account not found")

        check, risk_orders = self.risk_repository.get_with_orders(db, check_id=check_id)
        if check is None:
            raise ValueError("risk check not found")

        plan, targets = self.plan_repository.get_with_targets(db, plan_id=check.plan_id)
        if plan is None:
            raise ValueError("portfolio plan not found")
        target_by_symbol = {target.symbol: target for target in targets}

        fill_count = 0
        rejected_count = 0
        for risk_order in risk_orders:
            target = target_by_symbol.get(risk_order.symbol)
            if target is None:
                raise ValueError("portfolio target not found for risk order")

            order = BrokerOrderRequest(
                symbol=risk_order.symbol,
                side=risk_order.side,
                quantity=abs(risk_order.order_quantity),
                reference_price=target.price,
                trade_date=plan.as_of_date,
            )
            if not risk_order.passed:
                rejected_count += 1
                self.paper_repository.save_rejected_order(
                    db,
                    account_id=account.id,
                    plan_id=check.plan_id,
                    check_id=check_id,
                    order=order,
                    rejection_reason="risk_rejected",
                )
                continue

            fill = self.broker.execute(order)
            rejection_reason = self._validate_cash_and_position(db, account, fill)
            if rejection_reason is not None:
                rejected_count += 1
                self.paper_repository.save_rejected_order(
                    db,
                    account_id=account.id,
                    plan_id=check.plan_id,
                    check_id=check_id,
                    order=order,
                    rejection_reason=rejection_reason,
                )
                continue

            cash_balance, position_quantity, average_cost = self._apply_fill(
                db,
                account,
                fill,
            )
            self.paper_repository.save_filled_order(
                db,
                account=account,
                plan_id=check.plan_id,
                check_id=check_id,
                order=order,
                fill=fill,
                cash_balance=cash_balance,
                position_quantity=position_quantity,
                average_cost=average_cost,
            )
            account.cash_balance = cash_balance
            fill_count += 1

        return PaperTradingResult(
            account_id=account.id,
            check_id=check_id,
            order_count=len(risk_orders),
            fill_count=fill_count,
            rejected_count=rejected_count,
            cash_balance=account.cash_balance,
        )

    def _validate_cash_and_position(
        self,
        db: Session,
        account: object,
        fill: BrokerFill,
    ) -> str | None:
        position = self.paper_repository.get_position(
            db,
            account_id=account.id,
            symbol=fill.symbol,
        )
        if fill.side == "buy":
            cash_required = Decimal(fill.quantity) * fill.price + fill.commission
            return "insufficient_cash" if account.cash_balance < cash_required else None
        if position is None or position.quantity < fill.quantity:
            return "insufficient_position"
        return None

    def _apply_fill(
        self,
        db: Session,
        account: object,
        fill: BrokerFill,
    ) -> tuple[Decimal, int, Decimal]:
        position = self.paper_repository.get_position(
            db,
            account_id=account.id,
            symbol=fill.symbol,
        )
        current_quantity = 0 if position is None else position.quantity
        current_average_cost = Decimal() if position is None else position.average_cost

        amount = Decimal(fill.quantity) * fill.price
        if fill.side == "buy":
            new_quantity = current_quantity + fill.quantity
            new_average_cost = (
                Decimal(current_quantity) * current_average_cost + amount
            ) / Decimal(new_quantity)
            return account.cash_balance - amount - fill.commission, new_quantity, new_average_cost

        new_quantity = current_quantity - fill.quantity
        new_average_cost = Decimal() if new_quantity == 0 else current_average_cost
        return account.cash_balance + amount - fill.commission, new_quantity, new_average_cost
