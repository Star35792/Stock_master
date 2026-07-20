from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


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
