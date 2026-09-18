from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RiskLimits:
    max_symbol_weight: Decimal
    max_industry_weight: Decimal
    max_drawdown: Decimal
    max_amount_participation: Decimal
    max_price_deviation: Decimal | None = None
    max_order_amount: Decimal | None = None
    max_cycle_turnover: Decimal | None = None


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
    market_price: Decimal | None = None
    projected_cycle_turnover: Decimal | None = None

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
        risk_reducing_order = (
            order.side == "sell"
            and risk_reducing_symbol
            and risk_reducing_industry
        )
        rules = [
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
            self._drawdown_rule(
                order=order,
                limit=limits.max_drawdown,
                risk_reducing=risk_reducing_order,
            ),
            self._capacity_rule(
                order=order,
                limit=limits.max_amount_participation,
            ),
        ]
        if limits.max_price_deviation is not None:
            rules.append(
                self._price_deviation_rule(
                    order=order,
                    limit=limits.max_price_deviation,
                )
            )
        if limits.max_order_amount is not None:
            rules.append(
                self._max_order_amount_rule(
                    order=order,
                    limit=limits.max_order_amount,
                )
            )
        if limits.max_cycle_turnover is not None:
            rules.append(
                self._max_cycle_turnover_rule(
                    order=order,
                    limit=limits.max_cycle_turnover,
                    risk_reducing=risk_reducing_order,
                )
            )
        return OrderRiskDecision(
            symbol=order.symbol,
            side=order.side,
            order_quantity=order.order_quantity,
            passed=all(rule.passed for rule in rules),
            rules=tuple(rules),
        )

    @staticmethod
    def _price_deviation_rule(
        *,
        order: OrderRiskInput,
        limit: Decimal,
    ) -> RiskRuleResult:
        if order.market_price is None or order.market_price <= 0:
            return RiskRuleResult(
                "price_deviation",
                False,
                "price_deviation_data_missing",
                "market price is missing or non-positive",
            )
        deviation = abs(order.price - order.market_price) / order.market_price
        passed = deviation <= limit
        return RiskRuleResult(
            "price_deviation",
            passed,
            "passed" if passed else "price_deviation_exceeded",
            f"{deviation} <= {limit}" if passed else f"{deviation} > {limit}",
        )

    @staticmethod
    def _max_order_amount_rule(
        *,
        order: OrderRiskInput,
        limit: Decimal,
    ) -> RiskRuleResult:
        amount = abs(Decimal(order.order_quantity)) * order.price
        passed = amount <= limit
        return RiskRuleResult(
            "max_order_amount",
            passed,
            "passed" if passed else "max_order_amount_exceeded",
            f"{amount} <= {limit}" if passed else f"{amount} > {limit}",
        )

    @staticmethod
    def _max_cycle_turnover_rule(
        *,
        order: OrderRiskInput,
        limit: Decimal,
        risk_reducing: bool,
    ) -> RiskRuleResult:
        if order.projected_cycle_turnover is None:
            return RiskRuleResult(
                "max_cycle_turnover",
                False,
                "cycle_turnover_data_missing",
                "projected cycle turnover is missing",
            )
        passed = order.projected_cycle_turnover <= limit or risk_reducing
        return RiskRuleResult(
            "max_cycle_turnover",
            passed,
            "passed" if passed else "max_cycle_turnover_exceeded",
            (
                f"{order.projected_cycle_turnover} <= {limit}"
                if order.projected_cycle_turnover <= limit
                else "risk-reducing order allowed"
                if risk_reducing
                else f"{order.projected_cycle_turnover} > {limit}"
            ),
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
        risk_reducing: bool,
    ) -> RiskRuleResult:
        breached = order.current_drawdown <= -abs(limit)
        passed = not breached or risk_reducing
        return RiskRuleResult(
            "drawdown_stop",
            passed,
            "passed" if passed else "drawdown_stop_triggered",
            (
                "drawdown within limit"
                if not breached
                else "risk-reducing order allowed"
                if risk_reducing
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
        if (
            limits.max_price_deviation is not None
            and (limits.max_price_deviation <= 0 or limits.max_price_deviation > 1)
        ):
            raise ValueError("max_price_deviation must be between 0 and 1")
        if limits.max_order_amount is not None and limits.max_order_amount <= 0:
            raise ValueError("max_order_amount must be positive")
        if limits.max_cycle_turnover is not None and limits.max_cycle_turnover <= 0:
            raise ValueError("max_cycle_turnover must be positive")
