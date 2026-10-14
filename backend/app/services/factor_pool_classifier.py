"""因子池分级策略。

基于 :class:`FactorProfileSummary` 的 IC 证据，把因子划分为：

- ``core``（核心池）：多 horizon 有显著 IC 证据、覆盖率高、样本充足；
- ``observation``（观察池）：有初步证据但不足以进入核心池；
- ``frozen``（冻结池）：无证据、证据为负或样本不足，禁止进入新策略。

阈值通过 :class:`FactorPoolPolicy` 显式配置，默认值为项目初期建议，
后续应由 T2 决策评审根据实际诊断结果调整。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.services.factor_batch_profiler import FactorProfileSummary

FactorPool = Literal["core", "observation", "frozen"]


@dataclass(frozen=True)
class FactorPoolPolicy:
    """因子池分级阈值。

    所有阈值都是「且」的关系。``min_trade_dates`` 和
    ``min_coverage_rate`` 是进入任何池的前提；``min_rank_ic`` 决定
    核心池与观察池的边界。
    """

    min_trade_dates: int = 200
    min_coverage_rate: Decimal = Decimal("0.70")
    min_rank_ic_1d: Decimal = Decimal("0.01")
    min_rank_ic_5d: Decimal = Decimal("0.01")
    min_rank_ic_20d: Decimal = Decimal("0.01")

    @classmethod
    def conservative(cls) -> FactorPoolPolicy:
        """更保守的阈值，适合初期严格筛选。"""
        return cls(
            min_trade_dates=250,
            min_coverage_rate=Decimal("0.80"),
            min_rank_ic_1d=Decimal("0.02"),
            min_rank_ic_5d=Decimal("0.02"),
            min_rank_ic_20d=Decimal("0.02"),
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
        reasons: list[str] = []
        pool: FactorPool = "frozen"

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
            reasons.append(
                f"trade_date_count={summary.trade_date_count} "
                f"< {self.policy.min_trade_dates}"
            )
        if not has_minimum_coverage:
            reasons.append(
                f"mean_coverage_rate={coverage} "
                f"< {self.policy.min_coverage_rate}"
            )
        if not rank_ic_1d_ok:
            reasons.append(
                f"mean_rank_ic_1d={summary.mean_rank_ic_1d} "
                f"< {self.policy.min_rank_ic_1d}"
            )
        if not rank_ic_5d_ok:
            reasons.append(
                f"mean_rank_ic_5d={summary.mean_rank_ic_5d} "
                f"< {self.policy.min_rank_ic_5d}"
            )
        if not rank_ic_20d_ok:
            reasons.append(
                f"mean_rank_ic_20d={summary.mean_rank_ic_20d} "
                f"< {self.policy.min_rank_ic_20d}"
            )

        if has_minimum_history and has_minimum_coverage and all_rank_ic_ok:
            pool = "core"
        elif has_minimum_history and has_minimum_coverage and any_rank_ic_ok:
            pool = "observation"
            reasons = [reason for reason in reasons if "rank_ic" in reason]
        else:
            pool = "frozen"

        return FactorPoolClassification(
            factor_name=summary.factor_name,
            factor_version=summary.factor_version,
            category=summary.category,
            pool=pool,
            reasons=reasons,
            summary=summary,
        )

    @staticmethod
    def _meets_threshold(
        value: Decimal | None,
        threshold: Decimal,
    ) -> bool:
        return value is not None and value >= threshold
