from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class FactorSelectionBacktestRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    strategy_name: str = Field(default="factor_selection", min_length=1, max_length=100)
    factor_specs: list[FactorScoreSpecRequest] = Field(min_length=1)
    top_n: int = Field(gt=0)
    signal_lag: int = Field(default=1, ge=1)
    rebalance_rule: Literal["daily"] = "daily"
    holding_period: int = Field(default=1, gt=0)
    benchmark: str | None = Field(default=None, min_length=1, max_length=100)
    allowed_security_types: set[Literal["stock", "etf", "index"]] = Field(
        default_factory=lambda: {"stock"}
    )
    start_date: date
    end_date: date
    adjust_type: Literal["none", "qfq", "hfq"] = "qfq"
    initial_cash: Decimal = Field(gt=0)
    commission_rate: Decimal = Field(default=Decimal("0.0003"), ge=0)
    slippage_bps: Decimal = Field(default=Decimal("2"), ge=0)
    lot_size: int = Field(default=100, gt=0)
    code_version: str = Field(min_length=1)
    data_version: str = Field(min_length=1)
    validation_mode: Literal["none", "out_of_sample", "rolling"] = "none"
    training_start_date: date | None = None
    training_end_date: date | None = None


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


class BatchFactorSelectionBacktestRequest(BaseModel):
    runs: list[FactorSelectionBacktestRequest] = Field(min_length=2, max_length=20)


class BatchFactorSelectionBacktestItem(BaseModel):
    index: int
    status: Literal["success", "failed"]
    result: FactorSelectionBacktestResult | None = None
    error_message: str | None = None
