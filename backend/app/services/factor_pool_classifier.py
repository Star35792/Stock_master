"""因子池分级策略。

基于 :class:`FactorProfileSummary` 的 IC 证据，把因子划分为：

- ``core``（核心池）：多 horizon 有显著 IC 证据、覆盖率高、样本充足，
  **且**通过端点闸门与时点性（point-in-time）闸门；
- ``observation``（观察池）：有初步证据但不足以进入核心池；
- ``frozen``（冻结池）：无证据、证据为负、样本不足，或含前视信息，禁止
  进入新策略。

阈值通过 :class:`FactorPoolPolicy` 显式配置，默认值为项目初期建议，
后续应由 T2 决策评审根据实际诊断结果调整。

两道硬闸门
----------

**端点闸门（endpoint gate）**

秩 IC 只说明「因子值与未来收益单调相关」，不说明「极端分位可交易」。一个
因子可以拥有正的 IC，但它的最贵分位跑输最便宜分位——这种因子不可交易。
因此进入核心池要求 1 日多空（top−bottom 分位）价差**严格为正**。

**时点性闸门（PIT gate，fail-closed）**

因子定义携带 ``pit_status``：

* ``point_in_time`` —— 输入在交易日当天即已知；
* ``look_ahead``   —— 至少一项输入泄漏未来信息，**硬冻结**；
* ``unknown``      —— 尚未审计，**不得进入核心池**。

``unknown`` 是默认值，因此一个从未被审计的因子不会悄悄进入核心池。

**生命周期闸门**

因子定义携带 ``status``：``retired`` 表示该定义已被取代或已知有误
（例如方向性错误的 ``value_pe_ttm``），证据保留供审计，但因子硬冻结。

**代码版本闸门**

同一评估窗口的逐日画像必须来自恰好一个可审计的 ``code_version``；空值及
``local`` / ``unknown`` 占位不具备审计意义。混合旧、新诊断口径的均值只可
保留为观察证据，不得据此授予核心池资格。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.services.factor_batch_profiler import FactorProfileSummary

FactorPool = Literal["core", "observation", "frozen"]

#: 时点性状态取值。
PIT_POINT_IN_TIME = "point_in_time"
PIT_LOOK_AHEAD = "look_ahead"
PIT_UNKNOWN = "unknown"

#: 定义生命周期状态取值。
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"


@dataclass(frozen=True)
class FactorPoolPolicy:
    """因子池分级阈值。

    所有阈值都是「且」的关系。``min_trade_dates`` 和
    ``min_coverage_rate`` 是进入任何池的前提；``min_rank_ic`` 决定
    核心池与观察池的边界；``min_long_short_forward_return_1d`` 是端点闸门。
    """

    min_trade_dates: int = 200
    min_coverage_rate: Decimal = Decimal("0.70")
    min_rank_ic_1d: Decimal = Decimal("0.01")
    min_rank_ic_5d: Decimal = Decimal("0.01")
    min_rank_ic_20d: Decimal = Decimal("0.01")
    #: 端点闸门：1 日多空价差必须**严格大于**该阈值。默认 0，即要求为正。
    min_long_short_forward_return_1d: Decimal = Decimal("0")

    @classmethod
    def conservative(cls) -> FactorPoolPolicy:
        """更保守的阈值，适合初期严格筛选。"""
        return cls(
            min_trade_dates=250,
            min_coverage_rate=Decimal("0.80"),
            min_rank_ic_1d=Decimal("0.02"),
            min_rank_ic_5d=Decimal("0.02"),
            min_rank_ic_20d=Decimal("0.02"),
            min_long_short_forward_return_1d=Decimal("0.0005"),
        )


@dataclass(frozen=True)
class FactorPoolClassification:
    factor_name: str
    factor_version: str
    category: str
    pool: FactorPool
    reasons: list[str]
    summary: FactorProfileSummary


class FactorPoolClassifierService:
    """根据 IC 证据把因子分级到核心池/观察池/冻结池。"""

    def __init__(self, policy: FactorPoolPolicy | None = None) -> None:
        self.policy = policy or FactorPoolPolicy()

    def classify(
        self,
        summaries: list[FactorProfileSummary],
    ) -> list[FactorPoolClassification]:
        return [self._classify_one(summary) for summary in summaries]

    def _classify_one(
        self,
        summary: FactorProfileSummary,
    ) -> FactorPoolClassification:
        # 结构性问题（样本量、覆盖率、PIT、端点）：任何池都不能豁免。
        structural_reasons: list[str] = []
        # IC 门槛问题：只在观察池里作为「还差什么」的说明保留。
        ic_reasons: list[str] = []

        coverage = summary.mean_coverage_rate
        rank_ic_1d_ok = self._meets_threshold(
            summary.mean_rank_ic_1d, self.policy.min_rank_ic_1d
        )
        rank_ic_5d_ok = self._meets_threshold(
            summary.mean_rank_ic_5d, self.policy.min_rank_ic_5d
        )
        rank_ic_20d_ok = self._meets_threshold(
            summary.mean_rank_ic_20d, self.policy.min_rank_ic_20d
        )

        has_minimum_history = summary.trade_date_count >= self.policy.min_trade_dates
        has_minimum_coverage = (
            coverage is not None and coverage >= self.policy.min_coverage_rate
        )
        any_rank_ic_ok = rank_ic_1d_ok or rank_ic_5d_ok or rank_ic_20d_ok
        all_rank_ic_ok = rank_ic_1d_ok and rank_ic_5d_ok and rank_ic_20d_ok

        if not has_minimum_history:
            structural_reasons.append(
                f"trade_date_count={summary.trade_date_count} "
                f"< {self.policy.min_trade_dates}"
            )
        if not has_minimum_coverage:
            structural_reasons.append(
                f"mean_coverage_rate={coverage} "
                f"< {self.policy.min_coverage_rate}"
            )
        if not rank_ic_1d_ok:
            ic_reasons.append(
                f"mean_rank_ic_1d={summary.mean_rank_ic_1d} "
                f"< {self.policy.min_rank_ic_1d}"
            )
        if not rank_ic_5d_ok:
            ic_reasons.append(
                f"mean_rank_ic_5d={summary.mean_rank_ic_5d} "
                f"< {self.policy.min_rank_ic_5d}"
            )
        if not rank_ic_20d_ok:
            ic_reasons.append(
                f"mean_rank_ic_20d={summary.mean_rank_ic_20d} "
                f"< {self.policy.min_rank_ic_20d}"
            )

        # --- 端点闸门 ---------------------------------------------------
        endpoint_value = summary.mean_long_short_forward_return_1d
        endpoint_ok = self._passes_endpoint_gate(summary)
        if not summary.has_endpoint_evidence:
            structural_reasons.append("缺少端点（多空分位）证据，无法判断可交易性")
        elif not endpoint_ok:
            structural_reasons.append(
                f"mean_long_short_forward_return_1d={endpoint_value} "
                f"<= {self.policy.min_long_short_forward_return_1d}"
            )

        # --- 时点性闸门（fail-closed） ----------------------------------
        pit_status = (summary.pit_status or PIT_UNKNOWN).strip().lower()
        pit_is_clean = pit_status == PIT_POINT_IN_TIME
        pit_is_look_ahead = pit_status == PIT_LOOK_AHEAD
        if pit_is_look_ahead:
            structural_reasons.append(
                "pit_status=look_ahead: 因子输入含前视信息，禁止进入任何池"
            )
        elif not pit_is_clean:
            structural_reasons.append(
                f"pit_status={pit_status}: 未完成时点性审计，禁止进入核心池"
            )

        # --- 定义生命周期闸门 -------------------------------------------
        factor_status = (summary.factor_status or STATUS_ACTIVE).strip().lower()
        is_retired = factor_status == STATUS_RETIRED
        if is_retired:
            structural_reasons.append(
                "factor_status=retired: 定义已被取代或已知有误，仅保留证据供审计"
            )

        # --- 证据资格闸门（fail-closed） ---------------------------------
        evidence_eligibility = (summary.evidence_eligibility or "research_only").strip().lower()
        evidence_is_strategy_eligible = evidence_eligibility == "strategy_eligible"
        if not evidence_is_strategy_eligible:
            structural_reasons.append(
                "evidence_eligibility="
                f"{evidence_eligibility}: {summary.evidence_eligibility_reason}; "
                "不得进入核心池"
            )

        # --- 代码版本一致性闸门（fail-closed） -------------------------
        has_consistent_code_version = summary.has_consistent_code_version
        if not has_consistent_code_version:
            structural_reasons.append(
                f"code_version={summary.code_version!r}, "
                f"code_version_count={summary.code_version_count}: "
                "评估窗口混合或缺失代码版本，必须整窗重算后才能进入核心池"
            )

        # --- 池归属 -----------------------------------------------------
        if (
            has_minimum_history
            and has_minimum_coverage
            and all_rank_ic_ok
            and pit_is_clean
            and endpoint_ok
            and not is_retired
            and evidence_is_strategy_eligible
            and has_consistent_code_version
        ):
            pool: FactorPool = "core"
            reasons = structural_reasons + ic_reasons
        elif (
            has_minimum_history
            and has_minimum_coverage
            and any_rank_ic_ok
            and not pit_is_look_ahead
            and not is_retired
        ):
            pool = "observation"
            # 观察池里只保留「还差什么」：结构性问题 + 未达标的 IC horizon。
            reasons = structural_reasons + ic_reasons
        else:
            pool = "frozen"
            reasons = structural_reasons + ic_reasons

        return FactorPoolClassification(
            factor_name=summary.factor_name,
            factor_version=summary.factor_version,
            category=summary.category,
            pool=pool,
            reasons=reasons,
            summary=summary,
        )

    def _passes_endpoint_gate(self, summary: FactorProfileSummary) -> bool:
        """端点闸门：1 日多空价差必须严格大于阈值，且确实测量过。

        比较使用严格大于（``>``）而不是 ``>=``：阈值为 0 时的语义是
        「端点价差必须为正」，等于 0 的端点意味着多空组合没有可交易优势。
        """
        if not summary.has_endpoint_evidence:
            return False
        value = summary.mean_long_short_forward_return_1d
        if value is None:
            return False
        return value > self.policy.min_long_short_forward_return_1d

    @staticmethod
    def _meets_threshold(
        value: Decimal | None,
        threshold: Decimal,
    ) -> bool:
        return value is not None and value >= threshold
