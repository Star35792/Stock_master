from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PaperAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cash_balance: Decimal


class PaperEquitySnapshotCreateRequest(BaseModel):
    valuation_date: date


class PaperEquityComponentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    quantity: int
    average_cost: Decimal
    price: Decimal
    market_value: Decimal
    daily_bar_id: int
    source_name: str
    quality_status: str
    bar_trade_date: date
    adjust_type: Literal["none"]
    is_suspect: Literal[False]
    bar_updated_at: datetime


class PaperEquitySnapshotRead(BaseModel):
    snapshot_id: int
    account_id: int
    valuation_date: date
    cash_balance: Decimal
    positions_market_value: Decimal
    total_equity: Decimal
    observed_high_watermark: Decimal
    drawdown_since_monitoring_start: Decimal
    previous_snapshot_id: int | None
    monitoring_start_date: date
    observation_count: int
    history_status: Literal["bootstrap", "continuous", "gap"]
    account_snapshot_hash: str
    market_snapshot_hash: str
    valuation_hash: str
    valuation_policy: dict[str, str]
    code_version: str
    created_at: datetime
    execution_allowed: Literal[False] = False
    risk_evaluated: Literal[False] = False
    orders_created: Literal[False] = False
    access_scope: Literal["local_single_user"] = "local_single_user"
    components: list[PaperEquityComponentRead]


class PaperLedgerAuditIssueRead(BaseModel):
    code: str
    severity: Literal["warning", "error"]
    message: str
    execution_batch_id: int | None = None
    order_id: int | None = None
    fill_id: int | None = None
    symbol: str | None = None


class PaperLedgerPositionAuditRead(BaseModel):
    symbol: str
    expected_quantity: int
    actual_quantity: int
    quantity_difference: int
    expected_average_cost: Decimal
    actual_average_cost: Decimal
    average_cost_difference: Decimal
    consistent: bool


class PaperLedgerAuditRead(BaseModel):
    account_id: int
    account_name: str
    status: Literal["consistent", "inconsistent", "unverifiable"]
    actual_cash_balance: Decimal
    expected_cash_balance: Decimal | None
    cash_difference: Decimal | None
    fill_count: int
    completed_execution_batch_count: int
    unlinked_fill_count: int
    positions: list[PaperLedgerPositionAuditRead]
    issues: list[PaperLedgerAuditIssueRead]
    read_only: Literal[True] = True
    auto_repair_performed: Literal[False] = False
