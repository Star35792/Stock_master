"""IC 证据因子的目录注册。

有两类因子的因子值**不由常规因子服务产出**：

* 温层 ``factor.factor_value`` 里的价格量因子（``v2-qfq-2y``）；
* 温层 ``research.valuation_history`` 派生的价值因子（``v2-valhist``）。

它们的 IC 证据由专用脚本写入 ``factor.factor_profile``。若没有对应的
``factor_definition`` 行，批量画像的分类查表会落空：``category`` 退化为
``"unknown"``、``pit_status`` 退化为 ``"unknown"``，因子池分级器因此无法
判断该因子是否允许进入核心池。

两个必须遵守的约束
------------------

**``is_active=False``**

批量画像按 ``is_active=True`` 枚举候选。它会按定义里的 ``version`` 去取
因子值，而这些版本的因子值在温层里并不以该版本号存在（温层实际是
``v2-qfq-2y-noca``），于是批量画像会得到空截面，并用
``observation_count=0`` 的行覆盖既有的 IC 证据。因此这批定义一律
``is_active=False``：证据由专用脚本负责刷新。

**``pit_status`` 必须显式标注**

``pit_status`` 是因子池分级器的硬门槛，取值：

* ``point_in_time`` —— 输入在交易日当天即已知；
* ``look_ahead``   —— 至少一项输入泄漏未来信息，**硬冻结**；
* ``unknown``      —— 未审计，不得进入核心池（fail-closed 默认值）。

价格量因子只用当日及历史的价格/成交量序列，天然满足时点约束；价值因子的
时点性则由估值字段的切换口径决定（``PE_TTM`` 按披露日切换，
``PB_MRQ`` 按报告期末切换）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.repositories.factor import FactorDefinitionRepository
from app.services.basic_price_volume_factors import BASIC_FACTOR_SPECS

#: 温层价格量因子的版本号。因子值由温层 ``factor.factor_value`` 提供，
#: 版本号必须与 ``factor.factor_profile`` 中的证据版本逐字一致。
WARM_PRICE_VOLUME_VERSION = "v2-qfq-2y"

#: 价值因子的当前版本号（修正后证据）。
VALUE_FACTOR_VERSION = "v2-valhist"

#: 价值因子的退役版本号（保留供审计）。
RETIRED_VALUE_FACTOR_VERSION = "v1-valhist"

#: 时点性状态取值。
PIT_POINT_IN_TIME = "point_in_time"
PIT_LOOK_AHEAD = "look_ahead"

#: 定义生命周期状态取值。
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"


@dataclass(frozen=True)
class EvidenceFactorSpec:
    """一条 IC 证据因子的目录定义。"""

    name: str
    version: str
    display_name: str
    description: str
    category: str
    inputs: list[str] = field(default_factory=list)
    window_days: int | None = None
    pit_status: str = PIT_POINT_IN_TIME
    is_active: bool = False
    status: str = STATUS_ACTIVE
    dependencies: list[str] = field(default_factory=list)


#: 温层价格量因子：只用历史价格/成交量，时点安全。
_WARM_PRICE_VOLUME_NAMES = (
    "momentum_20",
    "momentum_60",
    "volatility_20",
    "downside_volatility_20",
    "max_drawdown_20",
    "liquidity_20",
    "volume_stability_20",
)


def _warm_price_volume_specs() -> list[EvidenceFactorSpec]:
    specs: list[EvidenceFactorSpec] = []
    by_name = {spec.name: spec for spec in BASIC_FACTOR_SPECS}
    for name in _WARM_PRICE_VOLUME_NAMES:
        source = by_name.get(name)
        if source is None:
            continue
        specs.append(
            EvidenceFactorSpec(
                name=name,
                version=WARM_PRICE_VOLUME_VERSION,
                display_name=source.display_name,
                description=(
                    f"{source.description}（温层 factor.factor_value 版本，"
                    "IC 证据由 compute_warm_factor_ic.py 产出）"
                ),
                category=source.category,
                inputs=list(source.inputs),
                window_days=source.window_days,
                pit_status=PIT_POINT_IN_TIME,
                is_active=False,
            )
        )
    return specs


_VALUE_SPECS: tuple[EvidenceFactorSpec, ...] = (
    EvidenceFactorSpec(
        name="value_earnings_yield",
        version=VALUE_FACTOR_VERSION,
        display_name="Earnings yield (1/PE_TTM)",
        description=(
            "正 PE 股票的盈利收益率 1/PE_TTM。价值家族的标准定义：数值越大"
            "越便宜。历史回填为事后观测，当前仅限研究证据。"
        ),
        category="valuation",
        inputs=["research.valuation_history.pe_ttm"],
        pit_status="unknown",
        is_active=False,
    ),
    EvidenceFactorSpec(
        name="value_pb_mrq",
        version=VALUE_FACTOR_VERSION,
        display_name="Negative PB (MRQ)",
        description=(
            "-PB_MRQ。PB 按报告期末切换，会在披露前最多 120 天泄漏资产负债表，"
            "因此被因子池分级器硬冻结。"
        ),
        category="valuation",
        inputs=["research.valuation_history.pb_mrq"],
        pit_status=PIT_LOOK_AHEAD,
        is_active=False,
    ),
    EvidenceFactorSpec(
        name="value_earnings_yield",
        version=RETIRED_VALUE_FACTOR_VERSION,
        display_name="Earnings yield (1/PE_TTM) [retired]",
        description=(
            "退役版本，保留供审计。覆盖率分母口径有缺陷（分子分母同取因子"
            "自身非空集），端点价差为负，已被 v2-valhist 取代。"
        ),
        category="valuation",
        inputs=["research.valuation_history.pe_ttm"],
        pit_status=PIT_POINT_IN_TIME,
        is_active=False,
        status=STATUS_RETIRED,
    ),
    EvidenceFactorSpec(
        name="value_pb_mrq",
        version=RETIRED_VALUE_FACTOR_VERSION,
        display_name="Negative PB (MRQ) [retired]",
        description=(
            "退役版本，保留供审计。覆盖率分母口径有缺陷，已被 v2-valhist 取代。"
        ),
        category="valuation",
        inputs=["research.valuation_history.pb_mrq"],
        pit_status=PIT_LOOK_AHEAD,
        is_active=False,
        status=STATUS_RETIRED,
    ),
    EvidenceFactorSpec(
        name="value_pe_ttm",
        version=RETIRED_VALUE_FACTOR_VERSION,
        display_name="Negative PE (TTM) [retired]",
        description=(
            "已退役：方向性错误。PE_TTM 在亏损股为负，-PE_TTM 把深度亏损公司"
            "映射成大正数，最便宜五分位 100% 是亏损股；该因子度量的是"
            "「亏得有多狠」而不是「有多便宜」。"
        ),
        category="valuation",
        inputs=["research.valuation_history.pe_ttm"],
        pit_status=PIT_POINT_IN_TIME,
        is_active=False,
        status=STATUS_RETIRED,
    ),
)


def all_evidence_factor_specs() -> list[EvidenceFactorSpec]:
    """返回全部 IC 证据因子的目录定义。"""
    return [*_warm_price_volume_specs(), *_VALUE_SPECS]


def register_evidence_factor_definitions(db: Session) -> dict[str, int]:
    """把 IC 证据因子注册进 ``factor.factor_definition``。

    幂等：``ensure`` 按 name+version 更新既有行。
    """
    repo = FactorDefinitionRepository()
    counts: dict[str, int] = {}
    for spec in all_evidence_factor_specs():
        repo.ensure(
            db,
            name=spec.name,
            display_name=spec.display_name,
            description=spec.description,
            category=spec.category,
            frequency="daily",
            inputs=list(spec.inputs),
            applicable_security_types=["stock"],
            window_days=spec.window_days,
            version=spec.version,
            dependencies=list(spec.dependencies),
            parameters_schema={},
            is_active=spec.is_active,
            pit_status=spec.pit_status,
            status=spec.status,
        )
        counts[spec.version] = counts.get(spec.version, 0) + 1
    db.flush()
    counts["total"] = len(all_evidence_factor_specs())
    return counts


def annotate_price_volume_pit_status(db: Session) -> int:
    """把价格量因子既有定义的 ``pit_status`` 标注为 ``point_in_time``。

    价格量因子只用当日及历史的价格/成交量序列，不含任何披露滞后信息，
    它们的时点性是确定的。批量画像按 ``is_active`` 枚举候选，因此这里
    只改 ``pit_status``，不碰 ``is_active``。

    返回被改动的行数。
    """
    repo = FactorDefinitionRepository()
    updated = 0
    for name in _WARM_PRICE_VOLUME_NAMES:
        definitions, _ = repo.list(db, name=name, limit=100)
        for definition in definitions:
            if definition.category != "price_volume_risk":
                continue
            if definition.pit_status == PIT_POINT_IN_TIME:
                continue
            definition.pit_status = PIT_POINT_IN_TIME
            updated += 1
    db.flush()
    return updated
