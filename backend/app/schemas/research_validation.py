from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class ResearchValidationReportRequest(BaseModel):
    run_ids: list[int] = Field(min_length=1, max_length=20)


class ResearchValidationWindowRead(BaseModel):
    index: int
    run_id: int
    status: Literal["success", "failed"]
    run_status: str
    error_message: str | None
    training_start_date: date
    training_end_date: date
    test_start_date: date
    test_end_date: date
    total_return: Decimal | None = None
    annualized_return: Decimal | None = None
    maximum_drawdown: Decimal | None = None
    annualized_volatility: Decimal | None = None
    sharpe_ratio: Decimal | None = None
    calmar_ratio: Decimal | None = None
    daily_win_rate: Decimal | None = None
    profit_loss_ratio: Decimal | None = None
    turnover_rate: Decimal | None = None
    total_commission: Decimal | None = None
    trade_count: int = 0
    benchmark_status: Literal[
        "available", "not_configured", "unavailable", "not_applicable"
    ]
    benchmark_return: Decimal | None = None
    excess_return: Decimal | None = None
    benchmark_error: str | None = None


class ResearchValidationAggregateRead(BaseModel):
    return_sample_count: int
    mean_total_return: Decimal | None
    median_total_return: Decimal | None
    minimum_total_return: Decimal | None
    maximum_total_return: Decimal | None
    total_return_standard_deviation: Decimal | None
    positive_return_ratio: Decimal | None
    worst_maximum_drawdown: Decimal | None
    sharpe_sample_count: int
    mean_sharpe_ratio: Decimal | None
    turnover_sample_count: int
    mean_turnover_rate: Decimal | None
    total_commission: Decimal
    total_trade_count: int
    benchmark_sample_count: int
    benchmark_coverage_ratio: Decimal | None
    mean_excess_return: Decimal | None
    positive_excess_return_ratio: Decimal | None


class ResearchValidationReportRead(BaseModel):
    validation_mode: Literal["out_of_sample", "rolling"]
    strategy_name: str
    run_ids: list[int]
    window_count: int
    successful_window_count: int
    failed_window_count: int
    success_ratio: Decimal
    contract_hash: str
    comparison_contract: dict[str, Any]
    data_version: str
    code_version: str
    benchmark: str | None
    overlapping_test_windows: bool
    training_artifact_verified: Literal[False] = False
    window_binding_status: Literal["recorded_only"] = "recorded_only"
    compounded_return_available: Literal[False] = False
    limitation: str
    windows: list[ResearchValidationWindowRead]
    aggregate: ResearchValidationAggregateRead
