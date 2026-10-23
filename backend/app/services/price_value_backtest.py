"""Persisted, single-stock historical simulations of explicit valuation assumptions."""

import json
from dataclasses import asdict
from datetime import UTC, datetime, time
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.backtest.price_value import ENGINE_VERSION, run_price_value_backtest
from app.config.settings import settings
from app.repositories.market import (
    CorporateActionRepository,
    DailyBarRepository,
    SecurityRepository,
    TradingCalendarRepository,
)
from app.repositories.strategy import BacktestRepository
from app.schemas.price_value_backtest import (
    PriceValueBacktestRequest,
    PriceValueBacktestResponse,
)
from app.services.backtest import BacktestDataChangedError
from app.services.price_value_research import SHANGHAI

STRATEGY_NAME = "price_value_assumption_v1"
BAR_FIELDS = (
    "symbol",
    "security_type",
    "trade_date",
    "adjust_type",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "limit_up",
    "limit_down",
    "is_suspended",
    "special_treatment_status",
    "source_name",
    "quality_status",
    "is_suspect",
    "selected_raw_id",
    "updated_at",
)
SECURITY_FIELDS = (
    "symbol",
    "security_type",
    "exchange",
    "currency",
    "industry",
    "is_active",
    "list_date",
    "delist_date",
)
LIMITATIONS = [
    "手工假设的历史模拟：首次可得时间为用户声明，未认证历史预测，不是样本外验证。",
    "行情使用当前可信版本；未重建历史修订、历史行业或幸存者样本，不代表无未来信息偏差。",
    "仅支持单只沪深主板普通股与现金；不自动加仓，过期估值保持持仓并记录复核。",
    "费用与滑点为显式研究假设；前一交易日成交量限制仅是容量代理，不模拟真实订单簿。",
    "公司行为区间拒绝运行；期末持仓按收盘估值，不假设已卖出，不计现金利息。",
    "未验证预期收益、组合风险预算或策略稳健性，结果不可用于模拟账户或实盘下单。",
]


def _json_snapshot(value):
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _digest(value) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class PriceValueBacktestService:
    def __init__(self) -> None:
        self.bars = DailyBarRepository()
        self.securities = SecurityRepository()
        self.calendar = TradingCalendarRepository()
        self.actions = CorporateActionRepository()
        self.runs = BacktestRepository()

    def _load_inputs(self, db: Session, payload: PriceValueBacktestRequest):
        security = self.securities.get_by_symbol(db, symbol=payload.symbol)
        if security is None:
            raise ValueError("security is missing from the trusted market layer")
        if (
            security.security_type != "stock"
            or security.currency != "CNY"
            or security.exchange != payload.symbol.rsplit(".", 1)[1]
            or not payload.symbol.startswith(
                ("600", "601", "603", "605", "000", "001", "002", "003")
            )
        ):
            raise ValueError("first release supports only CNY SH/SZ main-board ordinary shares")
        industry = (security.industry or "").strip().lower()
        if not industry or any(
            token in industry
            for token in ("银行", "保险", "证券", "金融", "信托", "bank", "insur", "financ")
        ):
            raise ValueError("non-financial industry classification is required")
        if (
            not security.is_active
            or security.list_date is None
            or security.list_date > payload.start_date
            or (security.delist_date is not None and security.delist_date <= payload.end_date)
        ):
            raise ValueError("security lifecycle does not cover the requested interval")
        actions = self.actions.list_for_symbols_date_range(
            db, symbols={payload.symbol}, start_date=payload.start_date, end_date=payload.end_date
        )
        if actions:
            raise ValueError(
                "interval contains corporate actions; event accounting is not implemented"
            )
        days = self.calendar.list_open_dates(
            db, exchange=security.exchange, start_date=payload.start_date, end_date=payload.end_date
        )
        if len(days) < 2 or days != sorted(set(days)):
            raise ValueError("at least two unique ordered trusted trading dates are required")
        rows = self.bars.list_for_factor_calculation(
            db,
            symbols=[payload.symbol],
            start_date=payload.start_date,
            end_date=payload.end_date,
            adjust_type="none",
            quality_status="trusted",
        )
        bars = [SimpleNamespace(**{key: getattr(row, key) for key in BAR_FIELDS}) for row in rows]
        bar_dates = {bar.trade_date for bar in bars}
        if len(bar_dates) != len(bars):
            raise ValueError("duplicate trusted unadjusted bar")
        if any(bar.is_suspended for bar in bars):
            raise ValueError(
                "market.daily_bar must not contain suspended-day bars; "
                "use market.security_status_event for suspension status"
            )
        for bar in bars:
            if (
                bar.symbol != payload.symbol
                or bar.security_type != "stock"
                or bar.adjust_type != "none"
                or bar.quality_status != "trusted"
                or bar.is_suspect
                or not bar.source_name
            ):
                raise ValueError("backtest requires trusted non-suspect stock bars with provenance")
            if any(
                value is None or not value.is_finite() or value <= 0
                for value in (bar.open, bar.high, bar.low, bar.close)
            ) or not (bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high):
                raise ValueError("invalid OHLC in trusted market slice")
            if any(
                value is None or not value.is_finite() or value < 0
                for value in (bar.volume, bar.amount)
            ):
                raise ValueError("complete non-negative volume and amount are required")
            if not bar.is_suspended and (bar.volume == 0 or bar.amount == 0):
                raise ValueError(
                    "non-suspended zero volume or amount is an unexplained trading state"
                )
            if (bar.special_treatment_status or "").strip().lower() not in {"normal", "none"}:
                raise ValueError(
                    "ST or unknown historical treatment status is outside this simulation"
                )
            if (
                any(
                    value is None or not value.is_finite() or value <= 0
                    for value in (bar.limit_up, bar.limit_down)
                )
                or bar.limit_down >= bar.limit_up
            ):
                raise ValueError(
                    "valid daily price limits are required; never assume unbounded tradability"
                )
        snapshot = _json_snapshot(
            {
                "bars": [vars(bar) for bar in bars],
                "security": {key: getattr(security, key) for key in SECURITY_FIELDS},
                "trade_dates": days,
                "corporate_actions": [],
            }
        )
        return bars, days, snapshot

    def run(
        self, db: Session, payload: PriceValueBacktestRequest, *, now: datetime | None = None
    ) -> PriceValueBacktestResponse:
        evaluated_at = now or datetime.now(UTC)
        if datetime.combine(payload.end_date, time(16), tzinfo=SHANGHAI) > evaluated_at:
            raise ValueError(
                "end_date must have completed its trading session (16:00 Asia/Shanghai)"
            )
        if any(item.available_at > evaluated_at for item in payload.assumptions):
            raise ValueError("assumption available_at cannot be in the future")
        bars, days, snapshot = self._load_inputs(db, payload)
        result, decisions = run_price_value_backtest(payload, bars, days)
        # Compare independent committed observations; never certify historical PIT.
        with Session(bind=db.get_bind()) as verification_db:
            _, _, after = self._load_inputs(verification_db, payload)
        if _digest(snapshot) != _digest(after):
            raise BacktestDataChangedError(
                "trusted market inputs changed during price-value simulation"
            )

        contract = {
            "strategy_name": STRATEGY_NAME,
            "engine_version": ENGINE_VERSION,
            "code_version": settings.app_code_version,
            "request": payload.model_dump(mode="json"),
            "market_snapshot": snapshot,
            "research_only": True,
            "pit_verified": False,
            "execution_eligible": False,
            "validation_mode": "none",
            "exit_policy": "base_value_or_thesis_broken_retry_until_filled",
            "expired_valuation_policy": "hold_and_review_no_new_buys",
            "capacity_policy": "previous_session_volume",
            "corporate_action_policy": "reject",
            "signal_cutoff": "16:00 Asia/Shanghai",
            "execution_time": "next_session_09:30_Asia/Shanghai",
            "calculation_precision_policy": "engine_decimal_80_valuation_10dp_half_up",
        }
        contract_hash = _digest(contract)
        peak = result.initial_cash
        maximum_drawdown = Decimal()
        for point in result.equity_curve:
            peak = max(peak, point.total_equity)
            maximum_drawdown = max(maximum_drawdown, (peak - point.total_equity) / peak)
        report = {
            "evaluated_at": evaluated_at,
            "contract_hash": contract_hash,
            "initial_cash": result.initial_cash,
            "final_equity": result.final_equity,
            "net_profit": result.final_equity - result.initial_cash,
            "total_return": result.total_return,
            "maximum_drawdown": maximum_drawdown,
            "final_cash": result.equity_curve[-1].cash,
            "trade_count": len(result.trades),
            "assumptions": payload.model_dump(mode="json"),
            "limitations": LIMITATIONS,
            "trades": [asdict(item) for item in result.trades],
            "equity_curve": [asdict(item) for item in result.equity_curve],
            "positions": [asdict(item) for item in result.positions],
            "decisions": [item.model_dump(mode="json") for item in decisions],
        }
        parameters = {
            **contract,
            "contract_hash": contract_hash,
            "data_snapshot": {
                "sha256": _digest(snapshot),
                "verification_status": "verified",
                "verification_scope": "independent_session",
                "pit_verified": False,
            },
            "price_value_report": _json_snapshot(report),
        }
        run = self.runs.create_run(
            db,
            name=payload.name,
            strategy_name=STRATEGY_NAME,
            parameters=parameters,
            code_version=settings.app_code_version,
            data_version=f"price-value-{_digest(snapshot)[:20]}",
            start_date=payload.start_date,
            end_date=payload.end_date,
            initial_cash=payload.initial_cash,
        )
        self.runs.save_result(db, run=run, result=result)
        return PriceValueBacktestResponse(run_id=run.id, **report)

    def get(self, db: Session, run_id: int) -> PriceValueBacktestResponse:
        run = self.runs.get_run(db, run_id=run_id)
        if run is None or run.strategy_name != STRATEGY_NAME:
            raise LookupError("price-value backtest not found")
        report = dict(run.parameters["price_value_report"])
        report.setdefault(
            "net_profit", str(Decimal(report["final_equity"]) - Decimal(report["initial_cash"]))
        )
        return PriceValueBacktestResponse(run_id=run.id, **report)
