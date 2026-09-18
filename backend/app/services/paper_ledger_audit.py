from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.trade.ledger_audit import PaperLedgerAuditRepository
from app.schemas.trade import (
    PaperLedgerAuditIssueRead,
    PaperLedgerAuditRead,
    PaperLedgerPositionAuditRead,
)

CASH_QUANTUM = Decimal("0.0001")
COST_QUANTUM = Decimal("0.000001")


class PaperLedgerAccountNotFound(LookupError):
    pass


class PaperLedgerAuditService:
    def __init__(self, repository: PaperLedgerAuditRepository | None = None) -> None:
        self.repository = repository or PaperLedgerAuditRepository()

    def audit_account(self, db: Session, *, account_id: int) -> PaperLedgerAuditRead:
        facts = self.repository.get_facts(db, account_id=account_id)
        if facts is None:
            raise PaperLedgerAccountNotFound("paper account not found")

        issues: list[PaperLedgerAuditIssueRead] = []
        orders_by_id = {order.id: order for order in facts.orders}
        fills_by_order_id = {fill.order_id: fill for fill in facts.fills}
        positions = self._rebuild_positions(facts.fills, issues)
        position_reads = self._compare_positions(positions, facts.positions, issues)

        for order in facts.orders:
            fill = fills_by_order_id.get(order.id)
            if order.status == "filled" and fill is None:
                issues.append(
                    PaperLedgerAuditIssueRead(
                        code="filled_order_missing_fill",
                        severity="error",
                        message="filled paper order has no fill record",
                        order_id=order.id,
                        symbol=order.symbol,
                    )
                )
        for fill in facts.fills:
            order = orders_by_id.get(fill.order_id)
            if order is None:
                issues.append(
                    PaperLedgerAuditIssueRead(
                        code="fill_missing_order",
                        severity="error",
                        message="paper fill has no matching order record",
                        fill_id=fill.id,
                        symbol=fill.symbol,
                    )
                )
            elif (
                order.status != "filled"
                or order.account_id != fill.account_id
                or order.symbol != fill.symbol
                or order.side != fill.side
                or order.order_quantity != fill.quantity
            ):
                issues.append(
                    PaperLedgerAuditIssueRead(
                        code="fill_order_mismatch",
                        severity="error",
                        message="paper fill does not match its order facts",
                        order_id=order.id,
                        fill_id=fill.id,
                        symbol=fill.symbol,
                    )
                )

        completed_batch_ids = {batch.id for batch in facts.completed_batches}
        unlinked_fill_count = sum(
            1
            for fill in facts.fills
            if (order := orders_by_id.get(fill.order_id)) is None
            or order.execution_batch_id not in completed_batch_ids
        )
        if unlinked_fill_count:
            issues.append(
                PaperLedgerAuditIssueRead(
                    code="fills_without_execution_batch",
                    severity="warning",
                    message=(
                        f"{unlinked_fill_count} fill(s) are not linked to an execution batch; "
                        "cash history is only partially verifiable"
                    ),
                )
            )

        expected_cash = self._audit_batches(
            facts.completed_batches,
            facts.fills,
            orders_by_id,
            issues,
        )
        actual_cash = Decimal(facts.account.cash_balance).quantize(
            CASH_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        cash_difference = None
        if expected_cash is None:
            issues.append(
                PaperLedgerAuditIssueRead(
                    code="cash_history_unverifiable",
                    severity="warning",
                    message="no completed execution batch anchors the current cash balance",
                )
            )
        else:
            cash_difference = (actual_cash - expected_cash).quantize(
                CASH_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            if cash_difference != 0:
                issues.append(
                    PaperLedgerAuditIssueRead(
                        code="account_cash_mismatch",
                        severity="error",
                        message="paper account cash differs from the latest completed batch",
                    )
                )

        status = "consistent"
        if any(issue.severity == "error" for issue in issues):
            status = "inconsistent"
        elif issues:
            status = "unverifiable"

        return PaperLedgerAuditRead(
            account_id=facts.account.id,
            account_name=facts.account.name,
            status=status,
            actual_cash_balance=actual_cash,
            expected_cash_balance=expected_cash,
            cash_difference=cash_difference,
            fill_count=len(facts.fills),
            completed_execution_batch_count=len(facts.completed_batches),
            unlinked_fill_count=unlinked_fill_count,
            positions=position_reads,
            issues=issues,
        )

    @staticmethod
    def _rebuild_positions(
        fills: list[Any],
        issues: list[PaperLedgerAuditIssueRead],
    ) -> dict[str, dict[str, Any]]:
        state: dict[str, dict[str, Any]] = {}
        for fill in fills:
            current = state.setdefault(
                fill.symbol,
                {"quantity": 0, "average_cost": Decimal()},
            )
            quantity = int(fill.quantity)
            price = Decimal(fill.price)
            commission = Decimal(fill.commission)
            if fill.side == "buy":
                quantity_after = current["quantity"] + quantity
                if quantity_after <= 0:
                    average_cost = Decimal()
                elif current["quantity"] < 0:
                    average_cost = (Decimal(quantity) * price + commission) / Decimal(
                        quantity_after
                    )
                else:
                    average_cost = (
                        Decimal(current["quantity"]) * current["average_cost"]
                        + Decimal(quantity) * price
                        + commission
                    ) / Decimal(quantity_after)
            elif fill.side == "sell":
                quantity_after = current["quantity"] - quantity
                if quantity_after < 0:
                    issues.append(
                        PaperLedgerAuditIssueRead(
                            code="negative_rebuilt_position",
                            severity="error",
                            message="sell fills exceed the reconstructed position quantity",
                            fill_id=fill.id,
                            symbol=fill.symbol,
                        )
                    )
                average_cost = Decimal() if quantity_after <= 0 else current["average_cost"]
            else:
                issues.append(
                    PaperLedgerAuditIssueRead(
                        code="unsupported_fill_side",
                        severity="error",
                        message=f"unsupported paper fill side: {fill.side}",
                        fill_id=fill.id,
                        symbol=fill.symbol,
                    )
                )
                continue
            current["quantity"] = quantity_after
            current["average_cost"] = average_cost.quantize(
                COST_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
        return state

    @staticmethod
    def _compare_positions(
        expected: dict[str, dict[str, Any]],
        actual_rows: list[Any],
        issues: list[PaperLedgerAuditIssueRead],
    ) -> list[PaperLedgerPositionAuditRead]:
        actual = {row.symbol: row for row in actual_rows}
        reads = []
        for symbol in sorted(set(expected) | set(actual)):
            expected_state = expected.get(
                symbol,
                {"quantity": 0, "average_cost": Decimal()},
            )
            actual_row = actual.get(symbol)
            actual_quantity = 0 if actual_row is None else int(actual_row.quantity)
            actual_cost = Decimal() if actual_row is None else Decimal(actual_row.average_cost)
            expected_cost = Decimal(expected_state["average_cost"]).quantize(
                COST_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            actual_cost = actual_cost.quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)
            quantity_difference = actual_quantity - int(expected_state["quantity"])
            cost_difference = (actual_cost - expected_cost).quantize(
                COST_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            consistent = quantity_difference == 0 and cost_difference == 0
            if not consistent:
                issues.append(
                    PaperLedgerAuditIssueRead(
                        code="position_state_mismatch",
                        severity="error",
                        message="stored paper position differs from fill reconstruction",
                        symbol=symbol,
                    )
                )
            reads.append(
                PaperLedgerPositionAuditRead(
                    symbol=symbol,
                    expected_quantity=int(expected_state["quantity"]),
                    actual_quantity=actual_quantity,
                    quantity_difference=quantity_difference,
                    expected_average_cost=expected_cost,
                    actual_average_cost=actual_cost,
                    average_cost_difference=cost_difference,
                    consistent=consistent,
                )
            )
        return reads

    @staticmethod
    def _audit_batches(
        batches: list[Any],
        fills: list[Any],
        orders_by_id: dict[int, Any],
        issues: list[PaperLedgerAuditIssueRead],
    ) -> Decimal | None:
        previous_cash_after: Decimal | None = None
        for batch in batches:
            cash_before = Decimal(batch.cash_before).quantize(
                CASH_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            cash_after = Decimal(batch.cash_after).quantize(
                CASH_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
            if previous_cash_after is not None and cash_before != previous_cash_after:
                issues.append(
                    PaperLedgerAuditIssueRead(
                        code="execution_batch_cash_gap",
                        severity="error",
                        message="execution batch cash_before does not continue prior cash_after",
                        execution_batch_id=batch.id,
                    )
                )
            batch_fills = [
                fill
                for fill in fills
                if (order := orders_by_id.get(fill.order_id)) is not None
                and order.execution_batch_id == batch.id
            ]
            batch_fills.sort(
                key=lambda fill: (
                    orders_by_id[fill.order_id].execution_index
                    if orders_by_id[fill.order_id].execution_index is not None
                    else -1,
                    fill.id,
                )
            )
            computed_cash = cash_before
            for fill in batch_fills:
                amount = Decimal(fill.quantity) * Decimal(fill.price)
                commission = Decimal(fill.commission)
                if fill.side == "buy":
                    computed_cash -= amount + commission
                elif fill.side == "sell":
                    computed_cash += amount - commission
                computed_cash = computed_cash.quantize(
                    CASH_QUANTUM,
                    rounding=ROUND_HALF_UP,
                )
            if len(batch_fills) != batch.fill_count:
                issues.append(
                    PaperLedgerAuditIssueRead(
                        code="execution_batch_fill_count_mismatch",
                        severity="error",
                        message="execution batch fill_count differs from linked fill records",
                        execution_batch_id=batch.id,
                    )
                )
            expected_commission = sum(
                (Decimal(fill.commission) for fill in batch_fills),
                Decimal(),
            ).quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)
            if expected_commission != Decimal(batch.total_commission).quantize(
                COST_QUANTUM,
                rounding=ROUND_HALF_UP,
            ):
                issues.append(
                    PaperLedgerAuditIssueRead(
                        code="execution_batch_commission_mismatch",
                        severity="error",
                        message="execution batch commission differs from linked fills",
                        execution_batch_id=batch.id,
                    )
                )
            if computed_cash != cash_after:
                issues.append(
                    PaperLedgerAuditIssueRead(
                        code="execution_batch_cash_mismatch",
                        severity="error",
                        message=(
                            "execution batch cash_after differs from linked fill reconstruction"
                        ),
                        execution_batch_id=batch.id,
                    )
                )
            previous_cash_after = cash_after
        return previous_cash_after
