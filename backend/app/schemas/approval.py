from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class PaperExecutionApprovalCreateRequest(BaseModel):
    expected_evaluation_hash: str = Field(min_length=64, max_length=64)
    confirmation_phrase: str = Field(min_length=1, max_length=100)


class PaperExecutionApprovalRevokeRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class PaperExecutionApprovalRead(BaseModel):
    approval_id: int
    risk_check_id: int
    plan_id: int
    account_id: int
    equity_snapshot_id: int
    stored_status: Literal["pending", "consumed", "expired", "revoked"]
    effective_status: Literal["pending", "consumed", "expired", "revoked"]
    approval_scope: Literal["paper_execution"]
    actor_type: Literal["local_single_user"]
    approved_by: str
    code_version: str
    evaluation_hash: str
    plan_request_hash: str
    equity_valuation_hash: str
    readiness_account_hash: str
    readiness_market_hash: str
    readiness_hash: str
    adv_snapshot_hash: str
    risk_policy_hash: str
    order_batch_hash: str
    approval_hash: str
    order_summary: list[dict[str, Any]]
    confirmation_policy: dict[str, Any]
    approved_at: datetime
    expires_at: datetime
    consumed_at: datetime | None
    revoked_at: datetime | None
    revocation_reason: str | None
    created_at: datetime
    execution_allowed: Literal[False] = False
    orders_created: Literal[False] = False
    consumable_later: bool
    access_scope: Literal["local_single_user"] = "local_single_user"


class PaperExecutionCreateRequest(BaseModel):
    expected_approval_hash: str = Field(min_length=64, max_length=64)


class PaperExecutionOrderRead(BaseModel):
    execution_index: int
    order_id: int
    fill_id: int
    risk_check_order_id: int
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    reference_price: Decimal
    fill_price: Decimal
    commission: Decimal
    cash_after: Decimal


class PaperExecutionRead(BaseModel):
    execution_id: int
    approval_id: int
    risk_check_id: int
    plan_id: int
    account_id: int
    equity_snapshot_id: int
    status: Literal["completed", "rejected"]
    execution_mode: Literal["paper"]
    actor_type: Literal["local_single_user"]
    executed_by: str
    code_version: str
    approval_hash: str
    evaluation_hash: str
    order_batch_hash: str
    execution_policy_hash: str
    account_state_hash_before: str
    account_state_hash_after: str | None
    execution_hash: str
    cash_before: Decimal | None
    cash_after: Decimal | None
    order_count: int
    fill_count: int
    total_commission: Decimal
    failure_code: str | None
    failure_message: str | None
    started_at: datetime
    completed_at: datetime
    created_at: datetime
    approval_status: Literal["consumed"] = "consumed"
    real_broker_submitted: Literal[False] = False
    next_step: Literal["capture_new_equity_snapshot"] = "capture_new_equity_snapshot"
    access_scope: Literal["local_single_user"] = "local_single_user"
    orders: list[PaperExecutionOrderRead]
