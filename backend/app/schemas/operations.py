from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DataQualityIssueSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset: str
    symbol: str | None
    trade_date: date | None
    issue_type: str
    severity: str
    status: str
    created_at: datetime


class JobLogSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_name: str
    job_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    rows_failed: int
    error_message: str | None


class FactorRunSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factor_name: str
    factor_version: str
    status: str
    input_start_date: date | None
    input_end_date: date | None
    started_at: datetime
    finished_at: datetime | None
    rows_written: int
    error_message: str | None


class BacktestRunSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    strategy_name: str
    status: str
    start_date: date
    end_date: date
    final_equity: Decimal | None
    total_return: Decimal | None
    started_at: datetime
    finished_at: datetime | None
    error_message: str | None


class PaperOrderSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    plan_id: int
    check_id: int
    symbol: str
    side: str
    order_quantity: int
    trade_date: date
    status: str
    rejection_reason: str | None
    created_at: datetime


class DataQualityOverview(BaseModel):
    issue_status_counts: dict[str, int]
    open_issue_severity_counts: dict[str, int]
    latest_issues: list[DataQualityIssueSnapshot]


class JobOverview(BaseModel):
    status_counts: dict[str, int]
    latest_jobs: list[JobLogSnapshot]


class FactorRunOverview(BaseModel):
    status_counts: dict[str, int]
    latest_runs: list[FactorRunSnapshot]


class BacktestRunOverview(BaseModel):
    status_counts: dict[str, int]
    latest_runs: list[BacktestRunSnapshot]


class PaperTradingOverview(BaseModel):
    account_count: int
    fill_count: int
    position_count: int
    order_status_counts: dict[str, int]
    latest_orders: list[PaperOrderSnapshot]


class OperationsOverview(BaseModel):
    generated_at: datetime
    data_quality: DataQualityOverview
    jobs: JobOverview
    factors: FactorRunOverview
    backtests: BacktestRunOverview
    paper_trading: PaperTradingOverview


class DataQualityFailure(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dataset: str
    raw_id: int | None
    symbol: str | None
    trade_date: date | None
    adjust_type: str | None
    source_name: str | None
    issue_type: str
    severity: str
    status: str
    description: str
    expected_value: dict[str, Any] | None
    actual_value: dict[str, Any] | None
    created_at: datetime


class JobFailure(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_name: str
    job_type: str
    batch_id: int | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    rows_read: int
    rows_inserted: int
    rows_updated: int
    rows_failed: int
    error_message: str | None
    metadata: dict[str, Any] | None = Field(validation_alias="metadata_")


class FactorRunFailure(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factor_name: str
    factor_version: str
    parameters: dict[str, Any]
    code_version: str
    input_start_date: date | None
    input_end_date: date | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    rows_written: int
    error_message: str | None
    metadata: dict[str, Any] | None = Field(validation_alias="metadata_")


class BacktestRunFailure(BaseModel):
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


class PaperOrderFailure(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    plan_id: int
    check_id: int
    symbol: str
    side: str
    order_quantity: int
    reference_price: Decimal
    trade_date: date
    status: str
    rejection_reason: str | None
    risk_current_drawdown: Decimal | None
    risk_limits: dict[str, Any] | None
    risk_code_version: str | None
    risk_order_rejection_reasons: list[str]
    risk_order_rule_results: list[dict[str, Any]]
    created_at: datetime


class DataQualityFailureSection(BaseModel):
    total: int
    limit: int
    has_more: bool
    items: list[DataQualityFailure]


class JobFailureSection(BaseModel):
    total: int
    limit: int
    has_more: bool
    items: list[JobFailure]


class FactorRunFailureSection(BaseModel):
    total: int
    limit: int
    has_more: bool
    items: list[FactorRunFailure]


class BacktestRunFailureSection(BaseModel):
    total: int
    limit: int
    has_more: bool
    items: list[BacktestRunFailure]


class PaperOrderFailureSection(BaseModel):
    total: int
    limit: int
    has_more: bool
    items: list[PaperOrderFailure]


class OperationsFailureReport(BaseModel):
    generated_at: datetime
    data_quality_issues: DataQualityFailureSection
    jobs: JobFailureSection
    factor_runs: FactorRunFailureSection
    backtest_runs: BacktestRunFailureSection
    paper_orders: PaperOrderFailureSection


class OperationsFailureDetail(BaseModel):
    failure_type: Literal[
        "data_quality_issue",
        "job",
        "factor_run",
        "backtest_run",
        "paper_order",
    ]
    data_quality_issue: DataQualityFailure | None = None
    job: JobFailure | None = None
    factor_run: FactorRunFailure | None = None
    backtest_run: BacktestRunFailure | None = None
    paper_order: PaperOrderFailure | None = None


class OperationsAlertItem(BaseModel):
    key: str
    severity: Literal["info", "warning", "critical"]
    count: int
    title: str
    detail: str


class OperationsAlertSummary(BaseModel):
    generated_at: datetime
    items: list[OperationsAlertItem]


class OperationsRerunPlan(BaseModel):
    failure_type: Literal[
        "data_quality_issue",
        "job",
        "factor_run",
        "backtest_run",
        "paper_order",
    ]
    failure_id: int
    supported: bool
    action_type: str
    reason: str
    parameters: dict[str, Any]
