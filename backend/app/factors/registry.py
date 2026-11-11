"""因子定义目录注册。

把所有因子服务中定义的 spec 预注册到 ``factor.factor_definition``，使因子工作台
成为完整目录，而不依赖"跑过才有定义"。只写定义，不计算因子值；幂等，可重复调用。

``composite`` (``decision_score``) 是运行时动态组合，依赖调用方传入的因子清单，
不在此预注册。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.factors.categories import FactorCategory
from app.factors.evidence_catalog import (
    annotate_price_volume_pit_status,
    register_evidence_factor_definitions,
)
from app.repositories.factor import FactorDefinitionRepository
from app.services.basic_price_volume_factors import (
    BASIC_FACTOR_SPECS,
    FACTOR_VERSION as BASIC_VERSION,
    SUPPORTED_SECURITY_TYPES as BASIC_TYPES,
)
from app.services.capital_flow_factors import (
    CAPITAL_FLOW_FACTOR_SPECS,
    FACTOR_VERSION as CAPITAL_VERSION,
    SUPPORTED_SECURITY_TYPES as CAPITAL_TYPES,
)
from app.services.crowding_factors import (
    ABNORMAL_VOLUME_FACTOR_NAME,
    COMPONENT_FACTOR_VERSION as CROWDING_COMPONENT_VERSION,
    FACTOR_NAME as CROWDING_NAME,
    FACTOR_VERSION as CROWDING_VERSION,
    SENTIMENT_FACTOR_NAME,
    SUPPORTED_SECURITY_TYPES as CROWDING_TYPES,
    TURNOVER_FACTOR_NAME,
)
from app.services.earnings_revision_factors import (
    EARNINGS_REVISION_FACTOR_SPECS,
    FACTOR_VERSION as EARNINGS_VERSION,
    LOOKBACK_DAYS as EARNINGS_LOOKBACK,
    SUPPORTED_SECURITY_TYPES as EARNINGS_TYPES,
)
from app.services.financial_factors import (
    FACTOR_VERSION as FINANCIAL_VERSION,
    FINANCIAL_FACTOR_SPECS,
    SUPPORTED_SECURITY_TYPES as FINANCIAL_TYPES,
)
from app.services.industry_factors import (
    FACTOR_VERSION as INDUSTRY_VERSION,
    INDUSTRY_FACTOR_SPECS,
    SUPPORTED_SECURITY_TYPES as INDUSTRY_TYPES,
)
from app.services.macro_factors import (
    DATASET as MACRO_DATASET,
    FACTOR_VERSION as MACRO_VERSION,
    MACRO_FACTOR_SPECS,
    SUPPORTED_SECURITY_TYPES as MACRO_TYPES,
    TREND_OBSERVATIONS,
    _uses_trend_window,
)
from app.services.policy_event_factors import (
    FACTOR_VERSION as POLICY_VERSION,
    LOOKBACK_DAYS as POLICY_LOOKBACK,
    POLICY_EVENT_FACTOR_SPECS,
    SUPPORTED_SECURITY_TYPES as POLICY_TYPES,
)
from app.services.sentiment_factors import (
    FACTOR_VERSION as SENTIMENT_VERSION,
    SENTIMENT_FACTOR_SPECS,
    SUPPORTED_SECURITY_TYPES as SENTIMENT_TYPES,
)
from app.services.valuation_factors import (
    FACTOR_VERSION as VALUATION_VERSION,
    SUPPORTED_SECURITY_TYPES as VALUATION_TYPES,
    VALUATION_FACTOR_SPECS,
)


def register_all_factor_definitions(db: Session) -> dict[str, int]:
    """把全部因子 spec 预注册为 factor_definition 目录条目。

    幂等：ensure 按 name+version upsert，重复调用只更新不新增行。

    除证据目录与价格量因子外，其余因子家族的 ``pit_status`` 保持默认的
    ``"unknown"``：它们尚未完成时点性审计，而因子池分级器对 ``unknown``
    fail-closed（不得进入核心池）。
    """
    repo = FactorDefinitionRepository()
    counts: dict[str, int] = {}

    def _ensure_specs(specs, *, version, security_types, inputs_of, window_of, key):
        for spec in specs:
            repo.ensure(
                db,
                name=spec.name,
                display_name=spec.display_name,
                description=spec.description,
                category=spec.category,
                frequency="daily",
                inputs=inputs_of(spec),
                applicable_security_types=list(security_types),
                window_days=window_of(spec),
                version=version,
                parameters_schema={},
            )
        counts[key] = len(specs)

    _ensure_specs(
        BASIC_FACTOR_SPECS,
        version=BASIC_VERSION,
        security_types=BASIC_TYPES,
        inputs_of=lambda s: list(s.inputs),
        window_of=lambda s: s.window_days,
        key="basic_price_volume",
    )
    _ensure_specs(
        FINANCIAL_FACTOR_SPECS,
        version=FINANCIAL_VERSION,
        security_types=FINANCIAL_TYPES,
        inputs_of=lambda s: list(s.inputs),
        window_of=lambda s: None,
        key="financial",
    )
    _ensure_specs(
        VALUATION_FACTOR_SPECS,
        version=VALUATION_VERSION,
        security_types=VALUATION_TYPES,
        inputs_of=lambda s: list(s.inputs),
        window_of=lambda s: None,
        key="valuation",
    )
    _ensure_specs(
        INDUSTRY_FACTOR_SPECS,
        version=INDUSTRY_VERSION,
        security_types=INDUSTRY_TYPES,
        inputs_of=lambda s: list(s.inputs),
        window_of=lambda s: s.horizon_days,
        key="industry",
    )
    _ensure_specs(
        SENTIMENT_FACTOR_SPECS,
        version=SENTIMENT_VERSION,
        security_types=SENTIMENT_TYPES,
        inputs_of=lambda s: list(s.inputs),
        window_of=lambda s: None,
        key="sentiment",
    )
    _ensure_specs(
        MACRO_FACTOR_SPECS,
        version=MACRO_VERSION,
        security_types=MACRO_TYPES,
        inputs_of=lambda s: [
            f"market.alternative_data_snapshot.{MACRO_DATASET}.{key}"
            for key in s.item_keys
        ],
        window_of=lambda s: TREND_OBSERVATIONS if _uses_trend_window(s.name) else None,
        key="macro",
    )
    _ensure_specs(
        POLICY_EVENT_FACTOR_SPECS,
        version=POLICY_VERSION,
        security_types=POLICY_TYPES,
        inputs_of=lambda s: list(s.inputs),
        window_of=lambda s: POLICY_LOOKBACK,
        key="policy",
    )
    _ensure_specs(
        EARNINGS_REVISION_FACTOR_SPECS,
        version=EARNINGS_VERSION,
        security_types=EARNINGS_TYPES,
        inputs_of=lambda s: list(s.inputs),
        window_of=lambda s: EARNINGS_LOOKBACK,
        key="earnings_revision",
    )
    _ensure_specs(
        CAPITAL_FLOW_FACTOR_SPECS,
        version=CAPITAL_VERSION,
        security_types=CAPITAL_TYPES,
        inputs_of=lambda s: list(s.inputs),
        window_of=lambda s: s.window_days,
        key="capital_flow",
    )

    # crowding: 固定复合因子
    component_inputs = [
        f"factor.{TURNOVER_FACTOR_NAME}:{CROWDING_COMPONENT_VERSION}",
        f"factor.{ABNORMAL_VOLUME_FACTOR_NAME}:{CROWDING_COMPONENT_VERSION}",
        f"factor.{SENTIMENT_FACTOR_NAME}:{CROWDING_COMPONENT_VERSION}",
    ]
    repo.ensure(
        db,
        name=CROWDING_NAME,
        display_name="Crowding score",
        description=(
            "equal-weight blend of turnover percentile, abnormal volume "
            "pressure and market sentiment regime"
        ),
        category=FactorCategory.CAPITAL_FLOW,
        frequency="daily",
        inputs=component_inputs,
        applicable_security_types=list(CROWDING_TYPES),
        window_days=None,
        version=CROWDING_VERSION,
        dependencies=component_inputs,
        parameters_schema={"cross_section_rank_method": "average_rank"},
    )
    counts["crowding"] = 1

    # IC 证据因子（温层价格量因子 + 价值因子）的目录条目。它们不参与
    # 批量画像（is_active=False），但必须有定义：否则批量画像的分类查表
    # 会落空，category 与 pit_status 都会退化为 "unknown"，因子池分级器
    # 就无法判断这些因子能否进入核心池。
    counts["evidence_factors"] = register_evidence_factor_definitions(db)["total"]
    counts["price_volume_pit_annotated"] = annotate_price_volume_pit_status(db)
    db.flush()
    counts["total"] = sum(
        value for key, value in counts.items() if key != "price_volume_pit_annotated"
    )
    return counts
