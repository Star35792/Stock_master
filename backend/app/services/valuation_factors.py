from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.factors.categories import FactorCategory
from app.repositories.factor import (
    FactorDefinitionRepository,
    FactorRunRepository,
    FactorValueRepository,
)
from app.repositories.fundamental import FinancialStatementRepository
from app.repositories.market import (
    DailyBarRepository,
    SecurityRepository,
    ValuationSnapshotRepository,
)

FACTOR_VERSION = "v1"
VALUE_QUANT = Decimal("0.0000000001")
LOOKBACK_DAYS = 14
SUPPORTED_SECURITY_TYPES = ("stock",)

_TOTAL_ASSETS = ("total_assets", "total_asset", "assets_total")
_TOTAL_LIABILITIES = (
    "total_liabilities",
    "total_liability",
    "total_liab",
    "total_debt",
)
_TOTAL_EQUITY = (
    "total_equity",
    "shareholders_equity",
    "total_shareholders_equity",
    "total_owner_equity",
    "equity_attributable_to_parent",
    "holder_equity_total",
)
_REVENUE = ("operating_revenue", "total_revenue", "revenue", "operating_income")
_NET_PROFIT = (
    "parent_holder_net_profit",
    "net_profit_parent_company",
    "net_profit",
    "net_income",
)
_DEDUCTED_NET_PROFIT = (
    "deducted_net_profit",
    "net_profit_after_deduction",
    "recurring_net_profit",
    "net_profit_excluding_non_recurring",
    "扣非净利润",
    "归属于母公司股东的扣除非经常性损益的净利润",
)
_OPERATING_PROFIT = (
    "operating_profit",
    "ebit",
    "earnings_before_interest_and_taxes",
)
_OPERATING_CASH_FLOW = (
    "net_cash_flows_from_operating_activities",
    "net_operate_cash_flow",
    "net_operating_cash_flow",
    "operating_cash_flow",
    "act_cash_flow_net",
)
_CAPITAL_EXPENDITURE = (
    "capital_expenditure",
    "capex",
    "cash_paid_for_fixed_assets_intangible_assets_and_other_long_term_assets",
)
_INTEREST_BEARING_DEBT = (
    "interest_bearing_debt",
    "interest_bearing_liabilities",
    "total_interest_bearing_debt",
    "total_interest_bearing_liabilities",
)
_SHORT_TERM_DEBT = (
    "short_term_borrowing",
    "short_term_borrowings",
    "short_term_debt",
)
_LONG_TERM_DEBT = (
    "long_term_borrowing",
    "long_term_borrowings",
    "long_term_debt",
)
_CASH_AND_EQUIVALENTS = (
    "cash_and_cash_equivalents",
    "cash_equivalents",
    "monetary_funds",
    "cash",
)
_EBITDA = (
    "ebitda",
    "earnings_before_interest_tax_depreciation_and_amortization",
)
_DEPRECIATION_AMORTIZATION = (
    "depreciation_and_amortization",
    "depreciation_amortization",
    "depr_amort",
)


@dataclass(frozen=True)
class ValuationFactorSpec:
    name: str
    display_name: str
    description: str
    inputs: tuple[str, ...]
    higher_is_better: bool = True
    category: str = FactorCategory.VALUATION


@dataclass(frozen=True)
class ValuationFactorRunResult:
    start_date: date
    end_date: date
    input_start_date: date
    valuation_rows: int
    statement_rows: int
    rows_by_factor: dict[str, int]
    run_ids: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_factor.values())


@dataclass(frozen=True)
class FinancialSnapshot:
    symbol: str
    report_date: date
    period_end: date
    fiscal_year: int
    fiscal_period: str
    metrics_by_statement: dict[str, dict[str, Any]]


VALUATION_FACTOR_SPECS = (
    ValuationFactorSpec(
        name="market_cap",
        display_name="Market cap",
        description="latest available total market capitalization",
        inputs=("market.valuation_snapshot.total_market_cap",),
    ),
    ValuationFactorSpec(
        name="earnings_yield",
        display_name="Earnings yield",
        description="latest reported net profit / latest available total market cap",
        inputs=(
            "fundamental.financial_statement.income.metrics",
            "market.valuation_snapshot.total_market_cap",
        ),
    ),
    ValuationFactorSpec(
        name="book_to_price",
        display_name="Book to price",
        description="latest reported shareholders equity / latest available total market cap",
        inputs=(
            "fundamental.financial_statement.balance.metrics",
            "market.valuation_snapshot.total_market_cap",
        ),
    ),
    ValuationFactorSpec(
        name="sales_to_price",
        display_name="Sales to price",
        description="latest reported revenue / latest available total market cap",
        inputs=(
            "fundamental.financial_statement.income.metrics",
            "market.valuation_snapshot.total_market_cap",
        ),
    ),
    ValuationFactorSpec(
        name="fcf_yield",
        display_name="FCF yield",
        description="latest reported free cash flow / latest available total market cap",
        inputs=(
            "fundamental.financial_statement.cash_flow.metrics",
            "market.valuation_snapshot.total_market_cap",
        ),
    ),
    ValuationFactorSpec(
        name="ev_to_ebitda",
        display_name="EV to EBITDA",
        description="enterprise value / latest reported EBITDA",
        inputs=(
            "fundamental.financial_statement.income.metrics",
            "fundamental.financial_statement.balance.metrics",
            "fundamental.financial_statement.cash_flow.metrics",
            "market.valuation_snapshot.total_market_cap",
        ),
        higher_is_better=False,
    ),
    ValuationFactorSpec(
        name="deducted_earnings_yield",
        display_name="Deducted earnings yield",
        description=(
            "latest reported deducted (recurring) net profit / "
            "latest available total market cap"
        ),
        inputs=(
            "fundamental.financial_statement.income.metrics",
            "market.valuation_snapshot.total_market_cap",
        ),
    ),
    ValuationFactorSpec(
        name="deducted_earnings_quality",
        display_name="Deducted earnings quality",
        description=(
            "deducted (recurring) net profit / reported net profit; "
            "higher means less non-recurring distortion"
        ),
        inputs=("fundamental.financial_statement.income.metrics",),
        higher_is_better=True,
    ),
    ValuationFactorSpec(
        name="industry_valuation_gap",
        display_name="Industry valuation gap",
        description="earnings-yield percentile minus current-industry median percentile",
        inputs=(
            "fundamental.financial_statement.income.metrics",
            "market.valuation_snapshot.total_market_cap",
            "market.security.industry",
        ),
    ),
)


class ValuationFactorService:
    def __init__(
        self,
        daily_bar_repository: DailyBarRepository | None = None,
        valuation_repository: ValuationSnapshotRepository | None = None,
        security_repository: SecurityRepository | None = None,
        financial_statement_repository: FinancialStatementRepository | None = None,
        definition_repository: FactorDefinitionRepository | None = None,
        run_repository: FactorRunRepository | None = None,
        value_repository: FactorValueRepository | None = None,
    ) -> None:
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.valuation_repository = valuation_repository or ValuationSnapshotRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.financial_statement_repository = (
            financial_statement_repository or FinancialStatementRepository()
        )
        self.definition_repository = definition_repository or FactorDefinitionRepository()
        self.run_repository = run_repository or FactorRunRepository()
        self.value_repository = value_repository or FactorValueRepository()

    def run(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
        factor_names: list[str] | None = None,
        adjust_type: str = "qfq",
        code_version: str = "local",
        release_id: str | None = None,
    ) -> ValuationFactorRunResult:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if adjust_type not in {"none", "qfq", "hfq"}:
            raise ValueError("adjust_type must be one of: none, qfq, hfq")

        specs = self._select_specs(factor_names)
        normalized_symbols = (
            sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
            if symbols
            else None
        )
        bars = self.daily_bar_repository.list_for_factor_calculation(
            db,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            symbols=normalized_symbols,
            quality_status="trusted",
        )
        bars_by_symbol: dict[str, list[Any]] = defaultdict(list)
        for bar in bars:
            if bar.security_type in SUPPORTED_SECURITY_TYPES:
                bars_by_symbol[bar.symbol].append(bar)

        input_start_date = start_date - timedelta(days=LOOKBACK_DAYS)
        valuation_rows = self.valuation_repository.list_available(
            db,
            start_date=input_start_date,
            end_date=end_date,
            symbols=normalized_symbols,
        )
        valuations_by_symbol = _group_by_symbol(valuation_rows)
        statement_symbols = (
            sorted(bars_by_symbol) if normalized_symbols is None else normalized_symbols
        )
        statements = self.financial_statement_repository.list_available(
            db,
            end_date=end_date,
            symbols=statement_symbols,
        )
        statements_by_symbol = _snapshots_by_symbol(statements)
        industries = self.security_repository.get_industries(db, set(bars_by_symbol))

        rows_by_factor: dict[str, int] = {}
        run_ids: dict[str, int] = {}
        for spec in specs:
            definition = self.definition_repository.ensure(
                db,
                name=spec.name,
                display_name=spec.display_name,
                description=spec.description,
                category=spec.category,
                frequency="daily",
                inputs=list(spec.inputs),
                applicable_security_types=list(SUPPORTED_SECURITY_TYPES),
                window_days=None,
                version=FACTOR_VERSION,
                parameters_schema={},
            )
            run = self.run_repository.create(
                db,
                definition=definition,
                parameters={
                    "adjust_type": adjust_type,
                    "quality_status": "trusted",
                    "symbols": normalized_symbols,
                    "target_start_date": start_date.isoformat(),
                    "target_end_date": end_date.isoformat(),
                    "availability_policy": (
                        "valuation_trade_date_and_report_date_before_trade_date"
                    ),
                    "lookback_days": LOOKBACK_DAYS,
                },
                code_version=code_version,
                release_id=release_id,
                input_start_date=input_start_date,
                input_end_date=end_date,
                metadata={
                    "missing_policy": "skip_missing_valuation_or_required_metric",
                    "applicable_security_types": list(SUPPORTED_SECURITY_TYPES),
                    "higher_is_better": spec.higher_is_better,
                },
            )
            rows_written = self._write_factor_values(
                db,
                spec=spec,
                bars_by_symbol=bars_by_symbol,
                valuations_by_symbol=valuations_by_symbol,
                statements_by_symbol=statements_by_symbol,
                industries=industries,
                run_id=run.id,
            )
            self.run_repository.update_status(
                db,
                run_id=run.id,
                status="success",
                rows_written=rows_written,
            )
            rows_by_factor[spec.name] = rows_written
            run_ids[spec.name] = run.id

        return ValuationFactorRunResult(
            start_date=start_date,
            end_date=end_date,
            input_start_date=input_start_date,
            valuation_rows=len(valuation_rows),
            statement_rows=len(statements),
            rows_by_factor=rows_by_factor,
            run_ids=run_ids,
        )

    def _select_specs(
        self, factor_names: list[str] | None
    ) -> tuple[ValuationFactorSpec, ...]:
        if not factor_names:
            return VALUATION_FACTOR_SPECS

        requested = {name.strip() for name in factor_names if name.strip()}
        if not requested:
            return VALUATION_FACTOR_SPECS
        available = {spec.name for spec in VALUATION_FACTOR_SPECS}
        unknown = sorted(requested - available)
        if unknown:
            raise ValueError(f"unknown factor names: {', '.join(unknown)}")
        return tuple(spec for spec in VALUATION_FACTOR_SPECS if spec.name in requested)

    def _write_factor_values(
        self,
        db: Session,
        *,
        spec: ValuationFactorSpec,
        bars_by_symbol: dict[str, list[Any]],
        valuations_by_symbol: dict[str, list[Any]],
        statements_by_symbol: dict[str, list[FinancialSnapshot]],
        industries: dict[str, str],
        run_id: int,
    ) -> int:
        rows_written = 0
        if spec.name == "industry_valuation_gap":
            gap_values = self._industry_valuation_gaps(
                bars_by_symbol=bars_by_symbol,
                valuations_by_symbol=valuations_by_symbol,
                statements_by_symbol=statements_by_symbol,
                industries=industries,
            )
        else:
            gap_values = {}
        for symbol, bars in bars_by_symbol.items():
            valuations = valuations_by_symbol.get(symbol, [])
            statements = statements_by_symbol.get(symbol, [])
            valuation_index = 0
            statement_index = 0
            current_valuation = None
            current_statement = None
            for bar in bars:
                # valuation snapshots are end-of-day artifacts (market cap from the
                # same close), so a same-day snapshot is usable for the bar's
                # trade_date without lookahead; strategies execute next open.
                while (
                    valuation_index < len(valuations)
                    and valuations[valuation_index].trade_date <= bar.trade_date
                ):
                    current_valuation = valuations[valuation_index]
                    valuation_index += 1
                while (
                    statement_index < len(statements)
                    and statements[statement_index].report_date < bar.trade_date
                ):
                    current_statement = statements[statement_index]
                    statement_index += 1
                if current_valuation is None:
                    continue
                if spec.name == "industry_valuation_gap":
                    value = gap_values.get((symbol, bar.trade_date))
                else:
                    value = self._calculate_value(
                        spec.name,
                        current_valuation,
                        current_statement,
                    )
                if value is None:
                    continue
                self.value_repository.upsert_value(
                    db,
                    symbol=symbol,
                    trade_date=bar.trade_date,
                    factor_name=spec.name,
                    factor_version=FACTOR_VERSION,
                    value=value.quantize(VALUE_QUANT),
                    run_id=run_id,
                )
                rows_written += 1
        return rows_written

    def _industry_valuation_gaps(
        self,
        *,
        bars_by_symbol: dict[str, list[Any]],
        valuations_by_symbol: dict[str, list[Any]],
        statements_by_symbol: dict[str, list[FinancialSnapshot]],
        industries: dict[str, str],
    ) -> dict[tuple[str, date], Decimal]:
        earnings_yield_by_date: dict[date, dict[str, Decimal]] = defaultdict(dict)
        for symbol, bars in bars_by_symbol.items():
            valuations = valuations_by_symbol.get(symbol, [])
            statements = statements_by_symbol.get(symbol, [])
            valuation_index = 0
            statement_index = 0
            current_valuation = None
            current_statement = None
            for bar in bars:
                while (
                    valuation_index < len(valuations)
                    and valuations[valuation_index].trade_date <= bar.trade_date
                ):
                    current_valuation = valuations[valuation_index]
                    valuation_index += 1
                while (
                    statement_index < len(statements)
                    and statements[statement_index].report_date < bar.trade_date
                ):
                    current_statement = statements[statement_index]
                    statement_index += 1
                if current_valuation is None or current_statement is None:
                    continue
                value = self._calculate_value(
                    "earnings_yield",
                    current_valuation,
                    current_statement,
                )
                if value is not None:
                    earnings_yield_by_date[bar.trade_date][symbol] = value

        result: dict[tuple[str, date], Decimal] = {}
        for trade_date, symbol_values in earnings_yield_by_date.items():
            percentiles = _cross_section_percentiles(symbol_values)
            industry_percentiles: dict[str, list[Decimal]] = defaultdict(list)
            for symbol, percentile in percentiles.items():
                industry = industries.get(symbol)
                if industry:
                    industry_percentiles[industry].append(percentile)
            industry_medians = {
                industry: _median(values)
                for industry, values in industry_percentiles.items()
                if values
            }
            for symbol, percentile in percentiles.items():
                industry = industries.get(symbol)
                median = industry_medians.get(industry or "")
                if median is not None:
                    result[(symbol, trade_date)] = percentile - median
        return result

    def _calculate_value(
        self,
        factor_name: str,
        valuation: Any,
        statement: FinancialSnapshot | None,
    ) -> Decimal | None:
        if factor_name == "market_cap":
            return valuation.total_market_cap
        if statement is None:
            return None
        if factor_name == "earnings_yield":
            return self._ratio(
                _metric(statement, "income", _NET_PROFIT),
                valuation.total_market_cap,
            )
        if factor_name == "book_to_price":
            return self._ratio(_equity(statement), valuation.total_market_cap)
        if factor_name == "sales_to_price":
            return self._ratio(
                _metric(statement, "income", _REVENUE),
                valuation.total_market_cap,
            )
        if factor_name == "fcf_yield":
            return self._ratio(_free_cash_flow(statement), valuation.total_market_cap)
        if factor_name == "ev_to_ebitda":
            return self._ratio(
                _enterprise_value(statement, valuation.total_market_cap),
                _ebitda(statement),
            )
        if factor_name == "deducted_earnings_yield":
            return self._ratio(
                _metric(statement, "income", _DEDUCTED_NET_PROFIT),
                valuation.total_market_cap,
            )
        if factor_name == "deducted_earnings_quality":
            return _deducted_earnings_quality(statement)
        raise ValueError(f"unsupported factor: {factor_name}")

    @staticmethod
    def _ratio(current: Decimal | None, denominator: Decimal | None) -> Decimal | None:
        if current is None or denominator is None or denominator <= 0:
            return None
        return current / denominator


def _group_by_symbol(rows: list[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[row.symbol].append(row)
    for values in grouped.values():
        values.sort(key=lambda row: row.trade_date)
    return dict(grouped)


def _cross_section_percentiles(values_by_symbol: dict[str, Decimal]) -> dict[str, Decimal]:
    if len(values_by_symbol) == 1:
        symbol = next(iter(values_by_symbol))
        return {symbol: Decimal("0.5")}
    sorted_values = sorted(values_by_symbol.items(), key=lambda item: (item[1], item[0]))
    result = {}
    for symbol, value in values_by_symbol.items():
        below = sum(other_value < value for _, other_value in sorted_values)
        equal = sum(other_value == value for _, other_value in sorted_values)
        average_rank = Decimal(below) + (Decimal(equal - 1) / Decimal(2))
        result[symbol] = average_rank / Decimal(len(sorted_values) - 1)
    return result


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal(2)


def _snapshots_by_symbol(rows: list[Any]) -> dict[str, list[FinancialSnapshot]]:
    grouped: dict[tuple[str, int, str, date, date], dict[str, dict[str, Any]]] = (
        defaultdict(dict)
    )
    for row in rows:
        key = (
            row.symbol,
            row.fiscal_year,
            row.fiscal_period,
            row.report_date,
            row.period_end,
        )
        grouped[key][row.statement_type] = row.metrics

    result: dict[str, list[FinancialSnapshot]] = defaultdict(list)
    for (
        symbol,
        fiscal_year,
        fiscal_period,
        report_date,
        period_end,
    ), metrics_by_statement in grouped.items():
        result[symbol].append(
            FinancialSnapshot(
                symbol=symbol,
                report_date=report_date,
                period_end=period_end,
                fiscal_year=fiscal_year,
                fiscal_period=fiscal_period,
                metrics_by_statement=metrics_by_statement,
            )
        )
    for snapshots in result.values():
        snapshots.sort(
            key=lambda snapshot: (
                snapshot.report_date,
                snapshot.period_end,
                snapshot.fiscal_year,
                snapshot.fiscal_period,
            )
        )
    return dict(result)


def _metric(
    snapshot: FinancialSnapshot,
    statement_type: str,
    aliases: tuple[str, ...],
) -> Decimal | None:
    metrics = snapshot.metrics_by_statement.get(statement_type, {})
    for alias in aliases:
        value = _decimal(metrics.get(alias))
        if value is not None:
            return value
    return None


def _equity(snapshot: FinancialSnapshot) -> Decimal | None:
    explicit = _metric(snapshot, "balance", _TOTAL_EQUITY)
    if explicit is not None:
        return explicit
    total_assets = _metric(snapshot, "balance", _TOTAL_ASSETS)
    total_liabilities = _metric(snapshot, "balance", _TOTAL_LIABILITIES)
    if total_assets is None or total_liabilities is None:
        return None
    return total_assets - total_liabilities


def _free_cash_flow(snapshot: FinancialSnapshot) -> Decimal | None:
    operating_cash_flow = _metric(snapshot, "cash_flow", _OPERATING_CASH_FLOW)
    capital_expenditure = _metric(snapshot, "cash_flow", _CAPITAL_EXPENDITURE)
    if operating_cash_flow is None or capital_expenditure is None:
        return None
    return operating_cash_flow - abs(capital_expenditure)


def _enterprise_value(
    snapshot: FinancialSnapshot,
    market_cap: Decimal | None,
) -> Decimal | None:
    if market_cap is None:
        return None
    debt = _interest_bearing_debt(snapshot)
    cash = _metric(snapshot, "balance", _CASH_AND_EQUIVALENTS)
    if debt is None or cash is None:
        return None
    return market_cap + debt - cash


def _interest_bearing_debt(snapshot: FinancialSnapshot) -> Decimal | None:
    explicit = _metric(snapshot, "balance", _INTEREST_BEARING_DEBT)
    if explicit is not None:
        return explicit
    parts = [
        _metric(snapshot, "balance", _SHORT_TERM_DEBT),
        _metric(snapshot, "balance", _LONG_TERM_DEBT),
    ]
    if any(value is not None for value in parts):
        return sum((value for value in parts if value is not None), Decimal())
    return None


def _ebitda(snapshot: FinancialSnapshot) -> Decimal | None:
    explicit = _metric(snapshot, "income", _EBITDA)
    if explicit is not None:
        return explicit
    operating_profit = _metric(snapshot, "income", _OPERATING_PROFIT)
    depreciation = _metric(snapshot, "cash_flow", _DEPRECIATION_AMORTIZATION)
    if operating_profit is None or depreciation is None:
        return None
    return operating_profit + depreciation


def _deducted_earnings_quality(snapshot: FinancialSnapshot) -> Decimal | None:
    """扣非净利润 / 净利润；>1 表示非经常性亏损，<1 表示非经常性收益。

    该比值越接近 1，说明利润受非经常性损益影响越小；>1 同样代表
    利润质量高（非经常性项目为负向），但这里按策略文档口径统一以
    扣非利润占净利润比例作为质量度量。
    """
    deducted = _metric(snapshot, "income", _DEDUCTED_NET_PROFIT)
    reported = _metric(snapshot, "income", _NET_PROFIT)
    if deducted is None or reported is None or reported == 0:
        return None
    return deducted / reported


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid financial metric value: {value}") from exc
