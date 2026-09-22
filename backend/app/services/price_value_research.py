"""Read-only, assumption-based FCFE research. Never emits tradable signals."""

import json
from datetime import UTC, datetime, time, timedelta, timezone
from hashlib import sha256

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.repositories.market import DailyBarRepository, SecurityRepository
from app.schemas.price_value import PriceValueRequest
from app.schemas.price_value_result import (
    PriceValueMarketSnapshot,
    PriceValueMetrics,
    PriceValueResult,
    PriceValueSecuritySnapshot,
    ResearchReason,
)
from app.strategies.price_value import calculate_price_metrics, calculate_valuation

MODEL_VERSION = "assumption_fcfe_v1"
SHANGHAI = timezone(timedelta(hours=8))
MAX_QUOTE_AGE_DAYS = 7


class PriceValueResearchService:
    def __init__(
        self,
        *,
        daily_bar_repository: DailyBarRepository | None = None,
        security_repository: SecurityRepository | None = None,
    ) -> None:
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.security_repository = security_repository or SecurityRepository()

    def evaluate(
        self,
        db: Session,
        payload: PriceValueRequest,
        *,
        now: datetime | None = None,
    ) -> PriceValueResult:
        evaluated_at = now or datetime.now(UTC)
        if evaluated_at.tzinfo is None:
            raise ValueError("evaluation clock must include a timezone")
        blockers: list[ResearchReason] = []
        reviews: list[ResearchReason] = []

        def block(code: str, message: str) -> None:
            blockers.append(ResearchReason(code=code, message=message))

        if payload.valuation_at > evaluated_at:
            block("future_valuation", "估值时点不能晚于当前时间。")
        if payload.assumptions_available_at > payload.valuation_at:
            block("future_assumptions", "预测假设在估值时点尚不可得，不能用于该时点研究。")
        if payload.valid_until < payload.valuation_at:
            block("expired_assumptions", "估值假设已过有效期，请先复核。")
        quote_cutoff = datetime.combine(payload.trade_date, time(16), tzinfo=SHANGHAI)
        if quote_cutoff > payload.valuation_at:
            block("quote_not_available", "本研究按北京时间16:00后使用当日完整日线。")
        quote_age = (payload.valuation_at.astimezone(SHANGHAI).date() - payload.trade_date).days
        if quote_age > MAX_QUOTE_AGE_DAYS:
            block("stale_quote", "报价距估值时点超过7个自然日，请选择更新的可信行情。")
        if payload.company_profile != "stable_non_financial":
            block("unsupported_model", "首版FCFE模型仅支持稳定现金流的非金融企业。")

        security = self.security_repository.get_by_symbol(db, symbol=payload.symbol)
        security_snapshot = None
        if security is None:
            block("security_missing", "可信证券主数据不存在。")
        else:
            security_snapshot = PriceValueSecuritySnapshot(
                **{
                    field: getattr(security, field)
                    for field in PriceValueSecuritySnapshot.model_fields
                }
            )
            if security.security_type != "stock" or security.exchange not in {"SH", "SZ", "BJ"}:
                block("unsupported_security", "首版只支持A股普通股。")
            if security.currency != payload.currency:
                block("currency_mismatch", "报价币种与预测现金流、股本估值币种不一致。")
            if not security.is_active:
                block("inactive_security", "证券当前非活跃；当前主数据不能认证历史可投资状态。")
            if security.list_date is None or security.list_date > payload.trade_date:
                block("listing_unverified", "上市日期缺失或晚于报价日期。")
            if security.delist_date is not None and security.delist_date <= payload.trade_date:
                block("delisted_security", "报价日期已进入退市后区间。")
            industry = (security.industry or "").strip().lower()
            if not industry:
                block("industry_missing", "行业信息缺失，无法核对模型适用范围。")
            elif any(
                token in industry
                for token in ("银行", "保险", "证券", "金融", "信托", "bank", "insur", "financ")
            ):
                block("financial_industry", "金融行业需要独立估值模型，不能使用本通用模型。")

        # Exact date, unadjusted quote; never fall back to latest or to raw/vendor data.
        bar = self.daily_bar_repository.get_by_key(
            db, symbol=payload.symbol, trade_date=payload.trade_date, adjust_type="none"
        )
        market_snapshot = None
        if bar is None:
            block("quote_missing", "指定日期缺少未复权可信行情，不使用替代或演示报价。")
        else:
            market_snapshot = PriceValueMarketSnapshot(
                **{field: getattr(bar, field) for field in PriceValueMarketSnapshot.model_fields}
            )
            if (
                bar.symbol != payload.symbol
                or bar.trade_date != payload.trade_date
                or bar.adjust_type != "none"
                or bar.security_type != "stock"
            ):
                block("quote_identity_mismatch", "行情标的、日期、证券类型或复权口径不匹配。")
            if bar.quality_status not in {"trusted", "manual_fixed"} or bar.is_suspect:
                block("untrusted_quote", "行情未通过可信层质量门槛。")
            if not bar.close.is_finite() or bar.close <= 0:
                block("invalid_quote", "报价必须为有限正数。")
            if not bar.source_name:
                block("quote_source_missing", "行情缺少来源信息。")
            if bar.updated_at is None or bar.updated_at.tzinfo is None:
                block("quote_time_unknown", "行情缺少可解释的版本时间。")
            elif bar.updated_at > payload.valuation_at:
                block(
                    "quote_revised_after_valuation",
                    "当前行情版本晚于估值时点，不能回填为当时已知。",
                )
            if bar.is_suspended:
                block("suspended", "该日停牌，不能以估值折价覆盖交易限制。")
            treatment = (bar.special_treatment_status or "").strip().lower()
            if treatment not in {"normal", "none"}:
                block("treatment_restricted_or_unknown", "该日ST/退市风险状态受限或未知。")
            if any(
                value is None or not value.is_finite() or value <= 0
                for value in (bar.volume, bar.amount)
            ):
                block("liquidity_missing", "当日成交量或成交额缺失/非正，无法核对流动性。")
            if bar.limit_up is not None and bar.close >= bar.limit_up:
                block("at_upper_limit", "报价已达当日涨停价，不能假设可以买入。")

        if payload.thesis_broken:
            block("thesis_broken", "投资论点已失效，需重新估值和风险复核。")
            reviews.append(ResearchReason(code="thesis_broken", message="论点失效，优先复核风险。"))

        valuation = None
        metrics = None
        if not blockers:
            valuation = calculate_valuation(payload)
            metrics = PriceValueMetrics(
                **calculate_price_metrics(
                    market_snapshot.close,
                    valuation["low"],
                    payload.required_margin,
                    payload.quantity,
                    payload.fees,
                )
            )
            if payload.has_position and market_snapshot.close >= valuation["base"]:
                reviews.append(
                    ResearchReason(
                        code="at_base_value", message="报价已达基准价值，请复核剩余回报。"
                    )
                )
        if payload.has_position and blockers:
            reviews.append(
                ResearchReason(
                    code="position_data_review", message="持仓估值依据受限，请复核；不自动清仓。"
                )
            )

        status = (
            "blocked"
            if blockers
            else "review_required"
            if reviews
            else "price_condition_met"
            if metrics.price_condition_met
            else "wait_for_price"
        )
        limitations = [
            ResearchReason(
                code="assumption_only",
                message="现金流和稀释股本均为用户预测假设，未认证为可信财务事实。",
            ),
            ResearchReason(
                code="pit_unverified",
                message="未验证财报公告、首次可得、修订血缘和历史行业；不构成历史策略回测。",
            ),
            ResearchReason(
                code="return_not_evaluated",
                message="尚未评估持有期净预期回报，FCFE不等于现金分红。",
            ),
            ResearchReason(
                code="portfolio_not_evaluated",
                message="尚未评估组合预算、ADV容量和下一交易时点成交条件。",
            ),
        ]
        contract = {
            "model_version": MODEL_VERSION,
            "code_version": settings.app_code_version,
            "assumptions": payload.model_dump(mode="json"),
            "market_snapshot": market_snapshot.model_dump(mode="json") if market_snapshot else None,
            "security_snapshot": (
                security_snapshot.model_dump(mode="json") if security_snapshot else None
            ),
        }
        digest = sha256(
            json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return PriceValueResult(
            status=status,
            existing_position_action="review" if payload.has_position and reviews else "unchanged",
            model_version=MODEL_VERSION,
            code_version=settings.app_code_version,
            evaluated_at=evaluated_at,
            contract_hash=digest,
            assumptions=payload,
            market_snapshot=market_snapshot,
            security_snapshot=security_snapshot,
            valuation=valuation,
            price_metrics=metrics,
            blocking_reasons=blockers,
            review_reasons=reviews,
            limitations=limitations,
        )
