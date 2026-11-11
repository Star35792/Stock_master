from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.strategies import MinimumScorePolicy, WeightingMode


class BacktestPortfolioConstraintsRequest(BaseModel):
    """Opt-in portfolio constraints for backtests, sourced from the same
    PortfolioBuilder the paper path uses.

    Only the pure-computation subset is exposed: style/tracking-error/
    volatility budgets need factor history joins and stay on the paper path
    for now. Enforcement is proportional scaling; score-ranked trim requires
    per-symbol scores the engine does not carry.
    """

    max_symbol_weight: Decimal = Field(default=Decimal("0.20"), gt=0, le=1)
    max_industry_weight: Decimal = Field(default=Decimal("0.40"), gt=0, le=1)
    cash_reserve_rate: Decimal = Field(default=Decimal("0.05"), ge=0, lt=1)
    security_type_budgets: dict[str, Decimal] | None = Field(default=None)


class BacktestRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    strategy_name: str
    parameters: dict[str, Any]
    code_version: str
    data_version: str
    start_date: date
    end_date: date
    initial_cash: Decimal
    final_equity: Decimal | None
    total_return: Decimal | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None
    created_at: datetime


class BacktestTradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    signal_date: date
    trade_date: date
    symbol: str
    side: str
    quantity: int
    reference_price: Decimal
    fill_price: Decimal
    commission: Decimal
    cash_after: Decimal
    created_at: datetime


class BacktestEquityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    trade_date: date
    cash: Decimal
    market_value: Decimal
    total_equity: Decimal
    created_at: datetime


class BacktestPositionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    trade_date: date
    symbol: str
    quantity: int
    close: Decimal
    market_value: Decimal
    created_at: datetime


class BacktestPerformanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    total_return: Decimal
    annualized_return: Decimal
    maximum_drawdown: Decimal
    annualized_volatility: Decimal
    sharpe_ratio: Decimal | None
    calmar_ratio: Decimal | None
    daily_win_rate: Decimal
    profit_loss_ratio: Decimal | None
    turnover_rate: Decimal
    total_commission: Decimal
    symbol_contributions: dict[str, Any]
    industry_exposure: dict[str, Any]
    style_exposure: dict[str, Any]
    code_version: str
    created_at: datetime


class BacktestBenchmarkRead(BaseModel):
    run_id: int
    benchmark: str
    strategy_total_return: Decimal
    benchmark_total_return: Decimal
    excess_return: Decimal
    strategy_annualized_return: Decimal
    benchmark_annualized_return: Decimal
    excess_annualized_return: Decimal
    start_date: date
    end_date: date


class BacktestComparisonRequest(BaseModel):
    run_ids: list[int] = Field(min_length=2, max_length=20)


class BacktestComparisonItem(BaseModel):
    run_id: int
    name: str
    strategy_name: str
    start_date: date
    end_date: date
    total_return: Decimal
    annualized_return: Decimal
    maximum_drawdown: Decimal
    sharpe_ratio: Decimal | None
    turnover_rate: Decimal


class StyleFactorRef(BaseModel):
    factor_name: str = Field(min_length=1)
    factor_version: str = Field(min_length=1)


class BacktestPerformanceAnalyzeRequest(BaseModel):
    code_version: str = Field(min_length=1)
    style_factors: dict[str, StyleFactorRef] = Field(default_factory=dict)


class FactorScoreSpecRequest(BaseModel):
    factor_name: str = Field(min_length=1)
    factor_version: str = Field(min_length=1)
    weight: Decimal = Decimal(1)
    higher_is_better: bool = True


class QuantileBacktestRequest(BaseModel):
    """Optional quantile (factor-layer) backtest add-on.

    When supplied, the run additionally splits the investable universe into
    ``n_layers`` mutually exclusive buckets by ``split_factor``'s cross-sectional
    rank on each decision date and runs one equal-weight long-only portfolio per
    bucket. The top-minus-bottom return is a *statistical* spread (A-shares
    cannot be shorted), never a tradable short position.
    """

    split_factor_name: str = Field(min_length=1)
    split_factor_version: str = Field(min_length=1)
    n_layers: int = Field(default=5, ge=2, le=20)
    max_assets_per_layer: int | None = Field(default=None, ge=1)
    weighting_mode: WeightingMode = WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED

    @model_validator(mode="after")
    def validate_weighting_mode(self) -> QuantileBacktestRequest:
        if self.weighting_mode is not WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED:
            raise ValueError(
                "quantile backtest only supports "
                "weighting_mode=equal_weight_fully_invested"
            )
        return self


class FactorSelectionBacktestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    strategy_name: str = Field(default="factor_selection", min_length=1, max_length=100)
    factor_specs: list[FactorScoreSpecRequest] = Field(min_length=1)
    top_n: int = Field(gt=0)
    signal_lag: int = Field(default=1, ge=1)
    rebalance_rule: Literal["daily", "weekly", "monthly", "every_n_trading_days"] = "daily"
    rebalance_interval: int = Field(default=1, ge=1, le=250)
    holding_period: int = Field(default=1, gt=0)
    benchmark: str | None = Field(default=None, min_length=1, max_length=100)
    allowed_security_types: set[Literal["stock", "etf", "index"]] = Field(
        default_factory=lambda: {"stock"}
    )
    coverage_ratio: Decimal | None = Field(default=None, gt=0, le=1)
    weighting_mode: WeightingMode = WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED
    minimum_score_policy: MinimumScorePolicy = MinimumScorePolicy.ALWAYS_FULLY_INVESTED
    minimum_score: Decimal | None = Field(default=None, ge=0, le=1)
    invested_weight: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    portfolio_constraints: BacktestPortfolioConstraintsRequest | None = None
    start_date: date
    end_date: date
    adjust_type: Literal["none", "qfq", "hfq"] = "qfq"
    initial_cash: Decimal = Field(gt=0)
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0)
    slippage_bps: Decimal = Field(default=Decimal("2"), ge=0)
    lot_size: int = Field(default=100, gt=0)
    fee_model_version: Literal["linear_commission_v1", "a_share_cost_v1"] = "linear_commission_v1"
    min_commission: Decimal | None = Field(default=None, ge=0)
    sell_stamp_duty_rate: Decimal | None = Field(default=None, ge=0)
    transfer_fee_rate: Decimal | None = Field(default=None, ge=0)
    exchange_fee_rate: Decimal | None = Field(default=None, ge=0)
    code_version: str = Field(min_length=1)
    data_version: str = Field(min_length=1)
    validation_mode: Literal["none", "out_of_sample", "rolling"] = "none"
    training_start_date: date | None = None
    training_end_date: date | None = None
    quantile_backtest: QuantileBacktestRequest | None = Field(
        default=None,
        description=(
            "Optional quantile (factor-layer) backtest add-on; when set, the run "
            "additionally splits the universe into n_layers by split_factor rank "
            "and runs one equal-weight long-only portfolio per bucket"
        ),
    )

    @model_validator(mode="after")
    def validate_weighting_mode_and_invested_weight(self) -> FactorSelectionBacktestRequest:
        if (
            self.weighting_mode is WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED
            and self.minimum_score_policy is MinimumScorePolicy.HOLD_CASH_WHEN_NO_CANDIDATE
            and self.invested_weight != Decimal("1")
        ):
            raise ValueError(
                "equal_weight_fully_invested with hold_cash_when_no_candidate "
                "always invests 100%; choose another weighting mode to leave cash"
            )
        if self.rebalance_rule != "every_n_trading_days" and self.rebalance_interval != 1:
            raise ValueError(
                "rebalance_interval only applies to rebalance_rule=every_n_trading_days"
            )
        return self


class FactorSelectionBacktestResult(BaseModel):
    run_id: int
    name: str
    strategy_name: str
    start_date: date
    end_date: date
    initial_cash: Decimal
    final_equity: Decimal
    total_return: Decimal
    trade_count: int
    equity_points: int
    position_snapshots: int
    quantile: QuantileBacktestResultRead | None = Field(
        default=None,
        description=(
            "Optional quantile (factor-layer) backtest result, present when the "
            "request carried quantile_backtest; the top-minus-bottom return is a "
            "statistical spread, not a tradable short position"
        ),
    )


class QuantileLayerRead(BaseModel):
    layer_index: int
    quantile_low: Decimal
    quantile_high: Decimal
    universe_size: int
    asset_count: int
    top_asset_symbols: list[str] = Field(default_factory=list)
    equity_curve: list[dict[str, Any]]
    total_return: Decimal
    annualized_return: Decimal
    maximum_drawdown: Decimal
    sharpe_ratio: Decimal | None
    turnover_rate: Decimal
    total_commission: Decimal


class QuantileBacktestResultRead(BaseModel):
    split_factor_name: str
    split_factor_version: str
    n_layers: int
    layers: list[QuantileLayerRead]
    long_short_spread_return: Decimal
    spread_curve: list[dict[str, Any]]
    declarations: dict[str, str]


class CombinedEquityCurveRead(BaseModel):
    trade_dates: list[date]
    total_equity: list[Decimal]
    total_return: Decimal
    annualized_return: Decimal
    sharpe_ratio: Decimal | None
    maximum_drawdown: Decimal
    turnover_rate: Decimal
    total_commission: Decimal
    window_count: int


class RollingBacktestResultRead(BaseModel):
    windows: list[FactorSelectionBacktestResult]
    combined: CombinedEquityCurveRead | None = None
    declarations: dict[str, str]


class BatchFactorSelectionBacktestRequest(BaseModel):
    runs: list[FactorSelectionBacktestRequest] = Field(min_length=2, max_length=20)


class BatchFactorSelectionBacktestItem(BaseModel):
    index: int
    status: Literal["success", "failed"]
    result: FactorSelectionBacktestResult | None = None
    error_message: str | None = None


class RollingWindowRequest(BaseModel):
    exchange: Literal["SH", "SZ", "BJ", "HK"]
    start_date: date
    end_date: date
    training_days: int = Field(gt=0, le=1000, description="Training-window open trading dates")
    test_days: int = Field(gt=0, le=1000, description="Test-window open trading dates")
    step_days: int = Field(gt=0, le=1000, description="Open trading dates between window starts")


class RollingWindowRead(BaseModel):
    index: int
    window_id: str
    training_start_date: date
    training_end_date: date
    test_start_date: date
    test_end_date: date
    execution_status: Literal["planning_only", "artifact_created", "run_completed"] = (
        "planning_only"
    )


class RollingFactorSelectionBacktestRequest(BaseModel):
    exchange: Literal["SH", "SZ", "BJ", "HK"]
    start_date: date
    end_date: date
    training_days: int = Field(gt=0, le=1000, description="Training-window open trading dates")
    test_days: int = Field(gt=0, le=1000, description="Test-window open trading dates")
    step_days: int = Field(gt=0, le=1000, description="Open trading dates between window starts")
    name: str = Field(min_length=1, max_length=100)
    strategy_name: str = Field(default="factor_selection", min_length=1, max_length=100)
    factor_specs: list[FactorScoreSpecRequest] = Field(min_length=1)
    top_n: int = Field(gt=0)
    signal_lag: int = Field(default=1, ge=1)
    rebalance_rule: Literal["daily", "weekly", "monthly", "every_n_trading_days"] = "daily"
    rebalance_interval: int = Field(default=1, ge=1, le=250)
    holding_period: int = Field(default=1, gt=0)
    benchmark: str | None = Field(default=None, min_length=1, max_length=100)
    allowed_security_types: set[Literal["stock", "etf", "index"]] = Field(
        default_factory=lambda: {"stock"}
    )
    coverage_ratio: Decimal | None = Field(default=None, gt=0, le=1)
    weighting_mode: WeightingMode = WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED
    minimum_score_policy: MinimumScorePolicy = MinimumScorePolicy.ALWAYS_FULLY_INVESTED
    minimum_score: Decimal | None = Field(default=None, ge=0, le=1)
    invested_weight: Decimal = Field(default=Decimal("1"), gt=0, le=1)
    portfolio_constraints: BacktestPortfolioConstraintsRequest | None = None
    adjust_type: Literal["none", "qfq", "hfq"] = "qfq"
    initial_cash: Decimal = Field(gt=0)
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0)
    slippage_bps: Decimal = Field(default=Decimal("2"), ge=0)
    lot_size: int = Field(default=100, gt=0)
    fee_model_version: Literal["linear_commission_v1", "a_share_cost_v1"] = "linear_commission_v1"
    min_commission: Decimal | None = Field(default=None, ge=0)
    sell_stamp_duty_rate: Decimal | None = Field(default=None, ge=0)
    transfer_fee_rate: Decimal | None = Field(default=None, ge=0)
    exchange_fee_rate: Decimal | None = Field(default=None, ge=0)
    code_version: str = Field(min_length=1)
    data_version: str = Field(min_length=1)
    selection_policy: Literal["training_ic_v1"] | None = Field(
        default=None,
        description=(
            "Optional per-window training selection policy; when set, the "
            "candidate factor_specs are re-scored on every training window "
            "and only artifact-bound selections drive the test window"
        ),
    )
    quantile_backtest: QuantileBacktestRequest | None = Field(
        default=None,
        description=(
            "Optional quantile (factor-layer) backtest add-on applied to every "
            "test window; the split_factor layer runs are not persisted as "
            "separate backtest runs - their curves and the top-minus-bottom "
            "spread are embedded in each window run's parameters"
        ),
    )

    @model_validator(mode="after")
    def validate_weighting_mode_and_invested_weight(
        self,
    ) -> RollingFactorSelectionBacktestRequest:
        if (
            self.weighting_mode is WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED
            and self.minimum_score_policy is MinimumScorePolicy.HOLD_CASH_WHEN_NO_CANDIDATE
            and self.invested_weight != Decimal("1")
        ):
            raise ValueError(
                "equal_weight_fully_invested with hold_cash_when_no_candidate "
                "always invests 100%; choose another weighting mode to leave cash"
            )
        return self


class BacktestDriftRequest(BaseModel):
    baseline_run_id: int = Field(gt=0)
    candidate_run_ids: list[int] = Field(min_length=1, max_length=19)
    return_tolerance: Decimal = Field(
        default=Decimal(0),
        ge=0,
        description="Allowed absolute total-return ratio difference",
    )
    drawdown_tolerance: Decimal = Field(
        default=Decimal(0),
        ge=0,
        description="Allowed absolute maximum-drawdown ratio difference",
    )


class BacktestDriftRead(BaseModel):
    baseline_run_id: int
    candidate_run_ids: list[int]
    return_tolerance: Decimal
    drawdown_tolerance: Decimal
    return_spread: Decimal
    drawdown_spread: Decimal
    drift_detected: bool


class BacktestFinalHoldingRead(BaseModel):
    symbol: str
    name: str | None = None
    industry: str | None = None
    security_type: str | None = None
    quantity: int
    close: Decimal
    market_value: Decimal
    weight: Decimal


class BacktestFinalHoldingsRead(BaseModel):
    run_id: int
    as_of_date: date
    cash: Decimal
    total_equity: Decimal
    holdings: list[BacktestFinalHoldingRead]


class StrategyDefinitionRead(BaseModel):
    """One registered built-in strategy definition."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    version: str
    display_name: str | None = None
    description: str | None = None
    category: str
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    locked_parameters: list[str] = Field(default_factory=list)
    required_margin: Decimal | None = None
    inputs: list[str] = Field(default_factory=list)
    is_active: bool = True
    status: str = "active"


class ValueInvestingScreenRequest(BaseModel):
    """Run the built-in value-investing screen.

    ``as_of`` selects a completed trading day; it must be strictly before
    today in Shanghai time, because the daily bar of the running session is a
    partial snapshot.  The screen takes no margin override on purpose: the
    margin is a locked parameter of the registered strategy definition.
    """

    as_of: date | None = None
