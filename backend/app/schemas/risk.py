from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RiskEvaluationCreateRequest(BaseModel):
    paper_account_id: int = Field(gt=0)
    equity_snapshot_id: int = Field(gt=0)


class RiskRuleEvaluationRead(BaseModel):
    rule_name: str
    passed: bool
    reason_code: str
    message: str


class RiskOrderEvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    symbol: str
    side: Literal["buy", "sell"]
    order_quantity: int
    passed: bool
    rejection_reasons: list[str]
    rule_results: list[RiskRuleEvaluationRead]
    reference_price: Decimal
    current_symbol_weight: Decimal
    projected_symbol_weight: Decimal
    current_industry_weight: Decimal
    projected_industry_weight: Decimal
    average_daily_amount: Decimal | None
    adv_window: int
    adv_observation_count: int
    adv_input_hash: str


class RiskEvaluationRead(BaseModel):
    check_id: int
    plan_id: int
    paper_account_id: int
    equity_snapshot_id: int
    evaluation_status: Literal["passed", "blocked", "failed"]
    risk_passed: bool
    current_drawdown: Decimal
    limits: dict[str, str]
    preconditions: list[dict[str, Any]]
    plan_request_hash: str
    readiness_account_hash: str
    readiness_market_hash: str
    readiness_hash: str
    adv_snapshot_hash: str
    risk_policy_hash: str
    evaluation_hash: str
    code_version: str
    created_at: datetime
    risk_evaluated: Literal[True] = True
    execution_allowed: Literal[False] = False
    orders_created: Literal[False] = False
    access_scope: Literal["local_single_user"] = "local_single_user"
    orders: list[RiskOrderEvaluationRead]
