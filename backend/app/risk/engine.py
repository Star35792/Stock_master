from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RiskLimits:
    max_symbol_weight: Decimal
    max_industry_weight: Decimal
    max_drawdown: Decimal
    max_amount_participation: Decimal


@dataclass(frozen=True)
class OrderRiskInput:
    symbol: str
    industry: str
    order_quantity: int
    price: Decimal
    current_symbol_weight: Decimal
    projected_symbol_weight: Decimal
    current_industry_weight: Decimal
    projected_industry_weight: Decimal
    average_daily_amount: Decimal | None
    current_drawdown: Decimal

    @property
    def side(self) -> str:
        return "buy" if self.order_quantity > 0 else "sell"


@dataclass(frozen=True)
class RiskRuleResult:
    rule_name: str
    passed: bool
    reason_code: str
    message: str


@dataclass(frozen=True)
class OrderRiskDecision:
    symbol: str
    side: str
    order_quantity: int
    passed: bool
    rules: tuple[RiskRuleResult, ...]

    @property
    def rejection_reasons(self) -> tuple[str, ...]:
        return tuple(rule.reason_code for rule in self.rules if not rule.passed)


class PreTradeRiskEngine:
    def check(
        self,
        *,
        order: OrderRiskInput,
        limits: RiskLimits,
    ) -> OrderRiskDecision:
        if order.order_quantity == 0:
            raise ValueError("order_quantity must not be zero")
        self._validate_limits(limits)
        risk_reducing_symbol = (
            order.projected_symbol_weight < order.current_symbol_weight
        )
        risk_reducing_industry = (
            order.projected_industry_weight < order.current_industry_weight
        )
        rules = (
            self._limit_rule(
                rule_name="max_symbol_weight",
                value=order.projected_symbol_weight,
                limit=limits.max_symbol_weight,
                risk_reducing=risk_reducing_symbol,
            ),
            self._limit_rule(
                rule_name="max_industry_weight",
                value=order.projected_industry_weight,
                limit=limits.max_industry_weight,
                risk_reducing=risk_reducing_industry,
            ),
            self._drawdown_rule(order=order, limit=limits.max_drawdown),
            self._capacity_rule(
                order=order,
                limit=limits.max_amount_participation,
            ),
        )
        return OrderRiskDecision(
            symbol=order.symbol,
            side=order.side,
            order_quantity=order.order_quantity,
            passed=all(rule.passed for rule in rules),
            rules=rules,
        )

    @staticmethod
    def _limit_rule(
        *,
        rule_name: str,
        value: Decimal,
        limit: Decimal,
        risk_reducing: bool,
    ) -> RiskRuleResult:
        passed = value <= limit or risk_reducing
        reason_code = "passed" if passed else f"{rule_name}_exceeded"
        message = (
            f"{value} <= {limit}"
            if value <= limit
            else "risk-reducing order allowed"
            if risk_reducing
            else f"{value} > {limit}"
        )
        return RiskRuleResult(rule_name, passed, reason_code, message)

    @staticmethod
    def _drawdown_rule(
        *,
        order: OrderRiskInput,
        limit: Decimal,
    ) -> RiskRuleResult:
        breached = order.current_drawdown <= -abs(limit)
        passed = not breached or order.side == "sell"
        return RiskRuleResult(
            "drawdown_stop",
            passed,
            "passed" if passed else "drawdown_stop_triggered",
            (
                "drawdown within limit"
                if not breached
                else "risk-reducing sell allowed"
                if order.side == "sell"
                else f"{order.current_drawdown} <= {-abs(limit)}"
            ),
        )

    @staticmethod
    def _capacity_rule(
        *,
        order: OrderRiskInput,
        limit: Decimal,
    ) -> RiskRuleResult:
        if order.average_daily_amount is None or order.average_daily_amount <= 0:
            return RiskRuleResult(
                "amount_capacity",
                False,
                "amount_capacity_data_missing",
                "average daily amount is missing or non-positive",
            )
        participation = (
            abs(Decimal(order.order_quantity)) * order.price
            / order.average_daily_amount
        )
        passed = participation <= limit
        return RiskRuleResult(
            "amount_capacity",
            passed,
            "passed" if passed else "amount_capacity_exceeded",
            f"{participation} <= {limit}" if passed else f"{participation} > {limit}",
        )

    @staticmethod
    def _validate_limits(limits: RiskLimits) -> None:
        for value in (
            limits.max_symbol_weight,
            limits.max_industry_weight,
            limits.max_drawdown,
            limits.max_amount_participation,
        ):
            if value <= 0 or value > 1:
                raise ValueError("risk limits must be between 0 and 1")
