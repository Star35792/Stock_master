from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from typing import Any

from sqlalchemy.orm import Session

from app.factors.categories import FactorCategory
from app.repositories.factor import (
    FactorDefinitionRepository,
    FactorRunRepository,
    FactorValueRepository,
)
from app.repositories.fundamental import (
    FinancialStatementRepository,
    FinancialStatementRevisionRepository,
    statement_content_hash,
)
from app.repositories.market import (
    DailyBarRepository,
    SecurityIndustryHistoryRepository,
    SecurityRepository,
)
from app.services.factor_universe import require_price_universe
from app.services.financial_sector import (
    EXCLUDED_FACTORS,
    POLICY_VERSION as FINANCIAL_SECTOR_POLICY_VERSION,
    is_financial_industry,
)

FACTOR_VERSION = "v1"
VALUE_QUANT = Decimal("0.0000000001")
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
_OPERATING_COST = (
    "operating_cost",
    "operating_costs",
    "total_operating_cost",
    "cost_of_goods_sold",
    "cogs",
)
_NET_PROFIT = (
    "parent_holder_net_profit",
    "net_profit_parent_company",
    "net_profit",
    "net_income",
)
_OPERATING_PROFIT = (
    "operating_profit",
    "ebit",
    "earnings_before_interest_and_taxes",
)
_INTEREST_EXPENSE = (
    "interest_expense",
    "interest_expenses",
    "interest_cost",
    "financial_expense",
    "financial_expenses",
    "finance_expense",
    "finance_expenses",
)
_CURRENT_ASSETS = (
    "current_assets",
    "current_asset",
    "total_current_assets",
    "current_assets_total",
)
_CURRENT_LIABILITIES = (
    "current_liabilities",
    "current_liability",
    "total_current_liabilities",
    "current_liabilities_total",
)
_INTEREST_BEARING_DEBT = (
    "interest_bearing_debt",
    "interest_bearing_liabilities",
    "total_interest_bearing_debt",
    "total_interest_bearing_liabilities",
    "interest_bearing_liab",
)
_SHORT_TERM_DEBT = (
    "short_term_borrowing",
    "short_term_borrowings",
    "short_term_loan",
    "short_term_loans",
    "short_term_debt",
)
_LONG_TERM_DEBT = (
    "long_term_borrowing",
    "long_term_borrowings",
    "long_term_loan",
    "long_term_loans",
    "long_term_debt",
)
_CURRENT_PORTION_LONG_DEBT = (
    "non_current_liabilities_due_within_one_year",
    "current_portion_of_long_term_debt",
)
_BONDS_PAYABLE = ("bonds_payable", "bond_payable", "notes_payable")
_OPERATING_CASH_FLOW = (
    "net_cash_flows_from_operating_activities",
    "net_operate_cash_flow",
    "net_operating_cash_flow",
    "operating_cash_flow",
    "act_cash_flow_net",
)
_ACCOUNTS_RECEIVABLE = (
    "accounts_receivable",
    "account_receivable",
    "notes_and_accounts_receivable",
    "receivables",
)
_INVENTORY = ("inventory", "inventories")


@dataclass(frozen=True)
class FinancialFactorSpec:
    name: str
    display_name: str
    description: str
    inputs: tuple[str, ...]
    category: str
    higher_is_better: bool = True


@dataclass(frozen=True)
class FinancialFactorRunResult:
    start_date: date
    end_date: date
    input_rows: int
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


FINANCIAL_FACTOR_SPECS = (
    FinancialFactorSpec(
        name="debt_to_assets",
        category=FactorCategory.FINANCIAL_HEALTH,
        display_name="Debt to assets",
        description="latest reported total liabilities / total assets",
        inputs=("fundamental.financial_statement.balance.metrics",),
        higher_is_better=False,
    ),
    FinancialFactorSpec(
        name="current_ratio",
        category=FactorCategory.FINANCIAL_HEALTH,
        display_name="Current ratio",
        description="latest reported current assets / current liabilities",
        inputs=("fundamental.financial_statement.balance.metrics",),
    ),
    FinancialFactorSpec(
        name="interest_coverage",
        category=FactorCategory.FINANCIAL_HEALTH,
        display_name="Interest coverage",
        description="latest reported operating profit / interest expense",
        inputs=("fundamental.financial_statement.income.metrics",),
    ),
    FinancialFactorSpec(
        name="operating_cash_debt",
        category=FactorCategory.FINANCIAL_HEALTH,
        display_name="Operating cash debt",
        description="latest reported operating cash flow / interest-bearing debt",
        inputs=(
            "fundamental.financial_statement.cash_flow.metrics",
            "fundamental.financial_statement.balance.metrics",
        ),
    ),
    FinancialFactorSpec(
        name="roe_ttm",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="ROE single-period proxy (legacy roe_ttm key)",
        description=(
            "latest reported single-period net profit / ending shareholders equity; not TTM"
        ),
        inputs=(
            "fundamental.financial_statement.income.metrics",
            "fundamental.financial_statement.balance.metrics",
        ),
    ),
    FinancialFactorSpec(
        name="roic_ttm",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="ROIC TTM",
        description="latest reported operating profit / invested capital",
        inputs=(
            "fundamental.financial_statement.income.metrics",
            "fundamental.financial_statement.balance.metrics",
        ),
    ),
    FinancialFactorSpec(
        name="roa_ttm",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="ROA TTM",
        description="latest reported net profit / total assets",
        inputs=(
            "fundamental.financial_statement.income.metrics",
            "fundamental.financial_statement.balance.metrics",
        ),
    ),
    FinancialFactorSpec(
        name="gross_margin_ttm",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="Gross margin TTM",
        description="latest reported gross profit / revenue",
        inputs=("fundamental.financial_statement.income.metrics",),
    ),
    FinancialFactorSpec(
        name="operating_margin_ttm",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="Operating margin TTM",
        description="latest reported operating profit / revenue",
        inputs=("fundamental.financial_statement.income.metrics",),
    ),
    FinancialFactorSpec(
        name="ocf_to_net_income_ttm",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="OCF to net income TTM",
        description="latest reported operating cash flow / net profit",
        inputs=(
            "fundamental.financial_statement.cash_flow.metrics",
            "fundamental.financial_statement.income.metrics",
        ),
    ),
    FinancialFactorSpec(
        name="accrual_ratio",
        category=FactorCategory.FINANCIAL_HEALTH,
        display_name="Accrual ratio",
        description="(net profit - operating cash flow) / average total assets",
        inputs=(
            "fundamental.financial_statement.income.metrics",
            "fundamental.financial_statement.cash_flow.metrics",
            "fundamental.financial_statement.balance.metrics",
        ),
        higher_is_better=False,
    ),
    FinancialFactorSpec(
        name="revenue_growth_yoy",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="Revenue growth YoY",
        description="latest reported revenue / comparable prior-year revenue - 1",
        inputs=("fundamental.financial_statement.income.metrics",),
    ),
    FinancialFactorSpec(
        name="ocf_growth_yoy",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="OCF growth YoY",
        description=(
            "latest reported operating cash flow / comparable prior-year operating cash flow - 1"
        ),
        inputs=("fundamental.financial_statement.cash_flow.metrics",),
    ),
    FinancialFactorSpec(
        name="net_profit_growth_yoy",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="Net profit growth YoY",
        description="latest reported net profit / comparable prior-year net profit - 1",
        inputs=("fundamental.financial_statement.income.metrics",),
    ),
    FinancialFactorSpec(
        name="roe_stability_3y",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="ROE stability 3Y",
        description="standard deviation of same-period ROE over latest three fiscal years",
        inputs=(
            "fundamental.financial_statement.income.metrics",
            "fundamental.financial_statement.balance.metrics",
        ),
        higher_is_better=False,
    ),
    FinancialFactorSpec(
        name="revenue_cagr_3y",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="Revenue CAGR 3Y",
        description="three-year compound annual growth rate of reported revenue",
        inputs=("fundamental.financial_statement.income.metrics",),
    ),
    FinancialFactorSpec(
        name="net_profit_cagr_3y",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="Net profit CAGR 3Y",
        description="three-year compound annual growth rate of reported net profit",
        inputs=("fundamental.financial_statement.income.metrics",),
    ),
    FinancialFactorSpec(
        name="receivable_growth_revenue_gap",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="Receivable growth revenue gap",
        description="accounts receivable YoY growth minus revenue YoY growth",
        inputs=(
            "fundamental.financial_statement.balance.metrics",
            "fundamental.financial_statement.income.metrics",
        ),
        higher_is_better=False,
    ),
    FinancialFactorSpec(
        name="inventory_growth_revenue_gap",
        category=FactorCategory.FUNDAMENTAL_QUALITY,
        display_name="Inventory growth revenue gap",
        description="inventory YoY growth minus revenue YoY growth",
        inputs=(
            "fundamental.financial_statement.balance.metrics",
            "fundamental.financial_statement.income.metrics",
        ),
        higher_is_better=False,
    ),
    FinancialFactorSpec(
        name="financial_anomaly_score",
        category=FactorCategory.FINANCIAL_HEALTH,
        display_name="Financial anomaly score",
        description="share of triggered balance-sheet and cash-flow anomaly checks",
        inputs=(
            "fundamental.financial_statement.balance.metrics",
            "fundamental.financial_statement.income.metrics",
            "fundamental.financial_statement.cash_flow.metrics",
        ),
        higher_is_better=False,
    ),
)


class FinancialFactorService:
    def __init__(
        self,
        daily_bar_repository: DailyBarRepository | None = None,
        financial_statement_repository: FinancialStatementRepository | None = None,
        financial_statement_revision_repository: (
            FinancialStatementRevisionRepository | None
        ) = None,
        security_repository: SecurityRepository | None = None,
        security_industry_history_repository: (SecurityIndustryHistoryRepository | None) = None,
        definition_repository: FactorDefinitionRepository | None = None,
        run_repository: FactorRunRepository | None = None,
        value_repository: FactorValueRepository | None = None,
    ) -> None:
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.financial_statement_repository = (
            financial_statement_repository or FinancialStatementRepository()
        )
        self.financial_statement_revision_repository = (
            financial_statement_revision_repository or FinancialStatementRevisionRepository()
        )
        self.security_repository = security_repository or SecurityRepository()
        self.security_industry_history_repository = (
            security_industry_history_repository or SecurityIndustryHistoryRepository()
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
    ) -> FinancialFactorRunResult:
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
                require_price_universe(
                    bars_by_symbol,
                    adjust_type=adjust_type,
                    start_date=start_date,
                    end_date=end_date,
                    factor_family='financial',
                    all_bars=bars,
                )

        statement_symbols = (
            sorted(bars_by_symbol) if normalized_symbols is None else normalized_symbols
        )
        statements = self.financial_statement_repository.list_available(
            db,
            end_date=end_date,
            symbols=statement_symbols,
        )
        snapshots_by_symbol = self._snapshots_by_symbol(statements)
        comparable_snapshots = {
            (snapshot.symbol, snapshot.fiscal_year, snapshot.fiscal_period): snapshot
            for snapshots in snapshots_by_symbol.values()
            for snapshot in snapshots
        }
        snapshots_by_symbol_period = self._snapshots_by_symbol_period(snapshots_by_symbol)

        # Point-in-time industry classification: resolve the label effective on
        # each trade date from history, falling back to the current label when
        # no history exists. The fallback is recorded, never silent.
        all_symbols = set(bars_by_symbol)
        current_industries = self.security_repository.get_industries(db, all_symbols)
        trade_dates = sorted({bar.trade_date for bars in bars_by_symbol.values() for bar in bars})
        industries_by_date: dict[date, dict[str, str]] = {}
        fallback_symbols: set[str] = set()
        unresolved_symbols: set[str] = set()
        for trade_date in trade_dates:
            pit = self.security_industry_history_repository.industries_as_of(
                db, symbols=all_symbols, as_of=trade_date
            )
            resolved: dict[str, str] = {}
            for symbol in all_symbols:
                industry = pit.get(symbol)
                if industry is None:
                    industry = current_industries.get(symbol) or None
                    if industry is not None:
                        fallback_symbols.add(symbol)
                if industry is None:
                    unresolved_symbols.add(symbol)
                    continue
                resolved[symbol] = industry
            industries_by_date[trade_date] = resolved
        if not trade_dates:
            # No bars means no factor rows; keep the classification honest by
            # resolving once at the run end date for metadata purposes.
            industries_by_date = {}
        financial_symbols = sorted(
            {
                symbol
                for resolved in industries_by_date.values()
                for symbol, industry in resolved.items()
                if is_financial_industry(industry)
            }
        )
        statement_digest = self._statement_content_digest(statements)
        revision_binding = self._statement_revision_binding(db, symbols=statement_symbols)

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
                    "availability_policy": "announcement_date_before_trade_date",
                },
                code_version=code_version,
                release_id=release_id,
                input_start_date=min((row.report_date for row in statements), default=None),
                input_end_date=end_date,
                metadata={
                    "missing_policy": "skip_missing_statement_or_required_metric",
                    "applicable_security_types": list(SUPPORTED_SECURITY_TYPES),
                    "higher_is_better": spec.higher_is_better,
                    # ``report_date`` is the supplier-provided announcement
                    # date; factors take effect strictly after it.
                    "report_date_semantics": "source_provided_announcement_date",
                    "statement_content_digest": statement_digest,
                    "statement_revision_binding": {
                        "policy": "latest_revision_row_hash",
                        "revisions": revision_binding,
                    },
                    "industry_classification": {
                        "policy": (
                            "pit_history_with_current_label_fallback"
                            if fallback_symbols
                            else "pit_history"
                        ),
                        "fallback_symbols": sorted(fallback_symbols),
                        "unresolved_symbols": sorted(unresolved_symbols),
                    },
                    "financial_sector_policy": {
                        "version": FINANCIAL_SECTOR_POLICY_VERSION,
                        "excluded_factors": sorted(EXCLUDED_FACTORS),
                        "excluded_symbols": financial_symbols,
                        "limitation": ("industry_specific_metrics_require_dedicated_data_source"),
                    },
                },
            )
            rows_written = self._write_factor_values(
                db,
                spec=spec,
                bars_by_symbol=bars_by_symbol,
                snapshots_by_symbol=snapshots_by_symbol,
                comparable_snapshots=comparable_snapshots,
                snapshots_by_symbol_period=snapshots_by_symbol_period,
                industries_by_date=industries_by_date,
                excluded_for_financials=spec.name in EXCLUDED_FACTORS,
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

        return FinancialFactorRunResult(
            start_date=start_date,
            end_date=end_date,
            input_rows=len(statements),
            rows_by_factor=rows_by_factor,
            run_ids=run_ids,
        )

    def _select_specs(self, factor_names: list[str] | None) -> tuple[FinancialFactorSpec, ...]:
        if not factor_names:
            return FINANCIAL_FACTOR_SPECS

        requested = {name.strip() for name in factor_names if name.strip()}
        if not requested:
            return FINANCIAL_FACTOR_SPECS
        available = {spec.name for spec in FINANCIAL_FACTOR_SPECS}
        unknown = sorted(requested - available)
        if unknown:
            raise ValueError(f"unknown factor names: {', '.join(unknown)}")
        return tuple(spec for spec in FINANCIAL_FACTOR_SPECS if spec.name in requested)

    @staticmethod
    def _statement_content_digest(statements: list[Any]) -> str:
        """Digest over the exact statement content consumed by this run.

        A later restatement changes the digest, so a factor run can always be
        checked against the statement content that existed when it executed.
        """

        parts = []
        for row in sorted(
            statements,
            key=lambda item: (
                item.symbol,
                item.statement_type,
                item.fiscal_year,
                item.fiscal_period,
                item.report_date,
            ),
        ):
            content_hash = statement_content_hash(
                {
                    "report_date": row.report_date,
                    "period_end": row.period_end,
                    "currency": row.currency,
                    "metrics": row.metrics,
                }
            )
            parts.append(
                "|".join(
                    (
                        row.symbol,
                        row.statement_type,
                        str(row.fiscal_year),
                        row.fiscal_period,
                        row.report_date.isoformat(),
                        content_hash,
                    )
                )
            )
        return sha256("\n".join(parts).encode("utf-8")).hexdigest()

    def _statement_revision_binding(
        self, db: Session, *, symbols: list[str] | None
    ) -> dict[str, str]:
        latest = self.financial_statement_revision_repository.latest_by_key(db, symbols=symbols)
        return {
            "|".join(
                (
                    symbol,
                    statement_type,
                    period,
                    str(fiscal_year),
                    fiscal_period,
                )
            ): revision.row_hash
            for (
                symbol,
                statement_type,
                period,
                fiscal_year,
                fiscal_period,
            ), revision in latest.items()
        }

    def _write_factor_values(
        self,
        db: Session,
        *,
        spec: FinancialFactorSpec,
        bars_by_symbol: dict[str, list[Any]],
        snapshots_by_symbol: dict[str, list[FinancialSnapshot]],
        comparable_snapshots: dict[tuple[str, int, str], FinancialSnapshot],
        snapshots_by_symbol_period: dict[tuple[str, str], list[FinancialSnapshot]],
        industries_by_date: dict[date, dict[str, str]],
        excluded_for_financials: bool,
        run_id: int,
    ) -> int:
        pending_rows: list[dict[str, Any]] = []
        for symbol, bars in bars_by_symbol.items():
            snapshots = snapshots_by_symbol.get(symbol, [])
            snapshot_index = 0
            current_snapshot = None
            for bar in bars:
                while (
                    snapshot_index < len(snapshots)
                    and snapshots[snapshot_index].report_date < bar.trade_date
                ):
                    current_snapshot = snapshots[snapshot_index]
                    snapshot_index += 1
                if current_snapshot is None:
                    continue
                # Financial-sector issuers are excluded from generic
                # structural factors; see app/services/financial_sector.py.
                if excluded_for_financials and is_financial_industry(
                    industries_by_date.get(bar.trade_date, {}).get(symbol)
                ):
                    continue

                value = self._calculate_value(
                    spec.name,
                    current_snapshot,
                    comparable_snapshots.get(
                        (
                            symbol,
                            current_snapshot.fiscal_year - 1,
                            current_snapshot.fiscal_period,
                        )
                    ),
                    snapshots_by_symbol_period.get(
                        (symbol, current_snapshot.fiscal_period),
                        [],
                    ),
                )
                if value is None:
                    continue
                pending_rows.append(
                    {
                        "symbol": symbol,
                        "trade_date": bar.trade_date,
                        "factor_name": spec.name,
                        "factor_version": FACTOR_VERSION,
                        "value": value.quantize(VALUE_QUANT),
                        "run_id": run_id,
                    }
                )
        return self.value_repository.upsert_values(db, pending_rows)

    def _calculate_value(
        self,
        factor_name: str,
        current: FinancialSnapshot,
        previous_year: FinancialSnapshot | None,
        history: list[FinancialSnapshot],
    ) -> Decimal | None:
        if factor_name == "debt_to_assets":
            return self._ratio(
                _metric(current, "balance", _TOTAL_LIABILITIES),
                _metric(current, "balance", _TOTAL_ASSETS),
            )
        if factor_name == "current_ratio":
            return self._ratio(
                _metric(current, "balance", _CURRENT_ASSETS),
                _metric(current, "balance", _CURRENT_LIABILITIES),
            )
        if factor_name == "interest_coverage":
            return self._ratio(
                _metric(current, "income", _OPERATING_PROFIT),
                _abs_metric(current, "income", _INTEREST_EXPENSE),
            )
        if factor_name == "operating_cash_debt":
            return self._ratio(
                _metric(current, "cash_flow", _OPERATING_CASH_FLOW),
                _interest_bearing_debt(current),
            )
        if factor_name == "roe_ttm":
            return self._ratio(
                _metric(current, "income", _NET_PROFIT),
                _equity(current),
            )
        if factor_name == "roic_ttm":
            return self._ratio(
                _metric(current, "income", _OPERATING_PROFIT),
                _invested_capital(current),
            )
        if factor_name == "roa_ttm":
            return self._ratio(
                _metric(current, "income", _NET_PROFIT),
                _metric(current, "balance", _TOTAL_ASSETS),
            )
        if factor_name == "gross_margin_ttm":
            revenue = _metric(current, "income", _REVENUE)
            cost = _metric(current, "income", _OPERATING_COST)
            if revenue is None or cost is None:
                return None
            return self._ratio(revenue - cost, revenue)
        if factor_name == "operating_margin_ttm":
            return self._ratio(
                _metric(current, "income", _OPERATING_PROFIT),
                _metric(current, "income", _REVENUE),
            )
        if factor_name == "ocf_to_net_income_ttm":
            return self._ratio(
                _metric(current, "cash_flow", _OPERATING_CASH_FLOW),
                _metric(current, "income", _NET_PROFIT),
            )
        if factor_name == "accrual_ratio":
            average_assets = _average_metric(current, previous_year, "balance", _TOTAL_ASSETS)
            net_profit = _metric(current, "income", _NET_PROFIT)
            operating_cash_flow = _metric(current, "cash_flow", _OPERATING_CASH_FLOW)
            if net_profit is None or operating_cash_flow is None:
                return None
            return self._ratio(net_profit - operating_cash_flow, average_assets)
        if factor_name == "revenue_growth_yoy":
            if previous_year is None:
                return None
            return self._ratio_change(
                _metric(current, "income", _REVENUE),
                _metric(previous_year, "income", _REVENUE),
            )
        if factor_name == "ocf_growth_yoy":
            if previous_year is None:
                return None
            return self._ratio_change(
                _metric(current, "cash_flow", _OPERATING_CASH_FLOW),
                _metric(previous_year, "cash_flow", _OPERATING_CASH_FLOW),
            )
        if factor_name == "net_profit_growth_yoy":
            if previous_year is None:
                return None
            return self._ratio_change(
                _metric(current, "income", _NET_PROFIT),
                _metric(previous_year, "income", _NET_PROFIT),
            )
        if factor_name == "roe_stability_3y":
            return _roe_stability_3y(current, history)
        if factor_name == "revenue_cagr_3y":
            return _cagr_3y(current, history, "income", _REVENUE)
        if factor_name == "net_profit_cagr_3y":
            return _cagr_3y(current, history, "income", _NET_PROFIT)
        if factor_name == "receivable_growth_revenue_gap":
            return _growth_gap(
                self,
                current,
                previous_year,
                "balance",
                _ACCOUNTS_RECEIVABLE,
                _REVENUE,
            )
        if factor_name == "inventory_growth_revenue_gap":
            return _growth_gap(
                self,
                current,
                previous_year,
                "balance",
                _INVENTORY,
                _REVENUE,
            )
        if factor_name == "financial_anomaly_score":
            return _financial_anomaly_score(self, current, previous_year)
        raise ValueError(f"unsupported factor: {factor_name}")

    @staticmethod
    def _snapshots_by_symbol(rows: list[Any]) -> dict[str, list[FinancialSnapshot]]:
        grouped: dict[tuple[str, int, str, date, date], dict[str, dict[str, Any]]] = defaultdict(
            dict
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

    @staticmethod
    def _snapshots_by_symbol_period(
        snapshots_by_symbol: dict[str, list[FinancialSnapshot]],
    ) -> dict[tuple[str, str], list[FinancialSnapshot]]:
        result: dict[tuple[str, str], list[FinancialSnapshot]] = defaultdict(list)
        for symbol, snapshots in snapshots_by_symbol.items():
            for snapshot in snapshots:
                result[(symbol, snapshot.fiscal_period)].append(snapshot)
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

    @staticmethod
    def _ratio(current: Decimal | None, denominator: Decimal | None) -> Decimal | None:
        if current is None or denominator is None or denominator == 0:
            return None
        return current / denominator

    def _ratio_change(
        self,
        current: Decimal | None,
        previous: Decimal | None,
    ) -> Decimal | None:
        ratio = self._ratio(current, previous)
        return None if ratio is None else ratio - Decimal(1)


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


def _abs_metric(
    snapshot: FinancialSnapshot,
    statement_type: str,
    aliases: tuple[str, ...],
) -> Decimal | None:
    value = _metric(snapshot, statement_type, aliases)
    return abs(value) if value is not None else None


def _average_metric(
    current: FinancialSnapshot,
    previous: FinancialSnapshot | None,
    statement_type: str,
    aliases: tuple[str, ...],
) -> Decimal | None:
    current_value = _metric(current, statement_type, aliases)
    if current_value is None:
        return None
    if previous is None:
        return current_value
    previous_value = _metric(previous, statement_type, aliases)
    if previous_value is None:
        return current_value
    return (current_value + previous_value) / Decimal(2)


def _interest_bearing_debt(snapshot: FinancialSnapshot) -> Decimal | None:
    explicit = _metric(snapshot, "balance", _INTEREST_BEARING_DEBT)
    if explicit is not None:
        return explicit
    components = [
        _metric(snapshot, "balance", aliases)
        for aliases in (
            _SHORT_TERM_DEBT,
            _LONG_TERM_DEBT,
            _CURRENT_PORTION_LONG_DEBT,
            _BONDS_PAYABLE,
        )
    ]
    available = [component for component in components if component is not None]
    if not available:
        return None
    return sum(available, Decimal())


def _invested_capital(snapshot: FinancialSnapshot) -> Decimal | None:
    equity = _equity(snapshot)
    debt = _interest_bearing_debt(snapshot)
    if equity is None or debt is None:
        return None
    return equity + debt


def _equity(snapshot: FinancialSnapshot) -> Decimal | None:
    explicit = _metric(snapshot, "balance", _TOTAL_EQUITY)
    if explicit is not None:
        return explicit
    total_assets = _metric(snapshot, "balance", _TOTAL_ASSETS)
    total_liabilities = _metric(snapshot, "balance", _TOTAL_LIABILITIES)
    if total_assets is None or total_liabilities is None:
        return None
    return total_assets - total_liabilities


def _roe(snapshot: FinancialSnapshot) -> Decimal | None:
    net_profit = _metric(snapshot, "income", _NET_PROFIT)
    equity = _equity(snapshot)
    if net_profit is None or equity in {None, Decimal()}:
        return None
    return net_profit / equity


def _roe_stability_3y(
    current: FinancialSnapshot,
    history: list[FinancialSnapshot],
) -> Decimal | None:
    visible_history = [
        snapshot
        for snapshot in history
        if snapshot.report_date <= current.report_date
        and snapshot.fiscal_year <= current.fiscal_year
    ]
    latest = sorted(
        visible_history,
        key=lambda snapshot: (snapshot.fiscal_year, snapshot.report_date),
        reverse=True,
    )[:3]
    if len(latest) < 3:
        return None
    roe_values = [_roe(snapshot) for snapshot in latest]
    if any(value is None for value in roe_values):
        return None
    values = [value for value in roe_values if value is not None]
    mean = sum(values, Decimal()) / Decimal(len(values))
    variance = sum(((value - mean) ** 2 for value in values), Decimal()) / Decimal(len(values))
    with localcontext() as context:
        context.prec = 28
        return variance.sqrt()


def _cagr_3y(
    current: FinancialSnapshot,
    history: list[FinancialSnapshot],
    statement_type: str,
    aliases: tuple[str, ...],
) -> Decimal | None:
    current_value = _metric(current, statement_type, aliases)
    if current_value is None or current_value <= 0:
        return None
    base = next(
        (
            snapshot
            for snapshot in history
            if snapshot.fiscal_year == current.fiscal_year - 3
            and snapshot.fiscal_period == current.fiscal_period
            and snapshot.report_date <= current.report_date
        ),
        None,
    )
    if base is None:
        return None
    base_value = _metric(base, statement_type, aliases)
    if base_value is None or base_value <= 0:
        return None
    with localcontext() as context:
        context.prec = 28
        return (current_value / base_value) ** (Decimal(1) / Decimal(3)) - Decimal(1)


def _growth_gap(
    service: FinancialFactorService,
    current: FinancialSnapshot,
    previous_year: FinancialSnapshot | None,
    balance_aliases_statement_type: str,
    balance_aliases: tuple[str, ...],
    revenue_aliases: tuple[str, ...],
) -> Decimal | None:
    if previous_year is None:
        return None
    balance_growth = service._ratio_change(
        _metric(current, balance_aliases_statement_type, balance_aliases),
        _metric(previous_year, balance_aliases_statement_type, balance_aliases),
    )
    revenue_growth = service._ratio_change(
        _metric(current, "income", revenue_aliases),
        _metric(previous_year, "income", revenue_aliases),
    )
    if balance_growth is None or revenue_growth is None:
        return None
    return balance_growth - revenue_growth


def _financial_anomaly_score(
    service: FinancialFactorService,
    current: FinancialSnapshot,
    previous_year: FinancialSnapshot | None,
) -> Decimal | None:
    checks = [
        _greater_than(
            service._ratio(
                _metric(current, "balance", _TOTAL_LIABILITIES),
                _metric(current, "balance", _TOTAL_ASSETS),
            ),
            Decimal("0.8"),
        ),
        _less_than(
            service._ratio(
                _metric(current, "balance", _CURRENT_ASSETS),
                _metric(current, "balance", _CURRENT_LIABILITIES),
            ),
            Decimal("1"),
        ),
        _less_than(
            service._ratio(
                _metric(current, "income", _OPERATING_PROFIT),
                _abs_metric(current, "income", _INTEREST_EXPENSE),
            ),
            Decimal("1"),
        ),
        _less_than(
            service._ratio(
                _metric(current, "cash_flow", _OPERATING_CASH_FLOW),
                _metric(current, "income", _NET_PROFIT),
            ),
            Decimal("0"),
        ),
        _greater_than(
            _growth_gap(
                service,
                current,
                previous_year,
                "balance",
                _ACCOUNTS_RECEIVABLE,
                _REVENUE,
            ),
            Decimal("0.2"),
        ),
        _greater_than(
            _growth_gap(
                service,
                current,
                previous_year,
                "balance",
                _INVENTORY,
                _REVENUE,
            ),
            Decimal("0.2"),
        ),
    ]
    available = [check for check in checks if check is not None]
    if not available:
        return None
    return Decimal(sum(available)) / Decimal(len(available))


def _greater_than(value: Decimal | None, threshold: Decimal) -> bool | None:
    return None if value is None else value > threshold


def _less_than(value: Decimal | None, threshold: Decimal) -> bool | None:
    return None if value is None else value < threshold


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid financial metric value: {value}") from exc
