"""批量因子截面诊断服务。

对 ``factor.factor_definition`` 中活跃的因子逐个运行
:class:`FactorCrossSectionService`，生成：

- 每个交易日截面的统计分布、IC/Rank IC、分层收益、换手；
- winsor / zscore / rank / industry_neutral 等横截面变换因子。

失败按因子隔离，不影响同批次其他因子；整批运行写入
``metadata.job_log``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.config.settings import Settings
from app.repositories.factor import FactorDefinitionRepository, FactorProfileRepository
from app.repositories.system import JobLogRepository
from app.services.factor_cross_section import FactorCrossSectionService

#: 默认计算远期收益时使用的复权口径，与研究回测默认一致。
DEFAULT_ADJUST_TYPE = "qfq"

#: 默认截面诊断回望窗口（交易日）。
DEFAULT_LOOKBACK_DAYS = 60

#: 默认 size 因子，用于 industry_size_neutral 变换。
DEFAULT_SIZE_FACTOR_NAME = "market_cap"
DEFAULT_SIZE_FACTOR_VERSION = "v1"


@dataclass(frozen=True)
class FactorProfileResult:
    factor_name: str
    factor_version: str
    category: str
    success: bool
    profile_rows: int = 0
    transformed_rows: int = 0
    run_ids: dict[str, int] = field(default_factory=dict)
    error_message: str | None = None


@dataclass(frozen=True)
class FactorBatchProfilerRunResult:
    start_date: date
    end_date: date
    adjust_type: str
    results: list[FactorProfileResult]
    total_profile_rows: int = 0
    total_transformed_rows: int = 0
    success_count: int = 0
    failed_count: int = 0

    @property
    def failed_factors(self) -> list[tuple[str, str, str | None]]:
        return [
            (r.factor_name, r.factor_version, r.error_message)
            for r in self.results
            if not r.success
        ]


@dataclass(frozen=True)
class FactorProfileSummary:
    """单个 (factor_name, factor_version) 的 IC 证据聚合。

    数值来自 ``factor.factor_profile`` 的逐日截面诊断，跨日期取简单平均；
    缺失 horizon 返回 ``None``，不与 0 混淆。

    ``pit_status`` 来自 ``factor.factor_definition``（``point_in_time`` /
    ``look_ahead`` / ``unknown``），是因子池分级的硬门槛：``look_ahead`` 与
    ``unknown`` 的因子不得进入核心池。
    """

    factor_name: str
    factor_version: str
    category: str
    trade_date_count: int
    first_trade_date: date
    last_trade_date: date
    #: Exact diagnostic implementation when the whole window is uniform.
    code_version: str | None = None
    #: Distinct implementations represented in the aggregated date window.
    code_version_count: int = 0
    mean_coverage_rate: Decimal | None = None
    mean_ic_1d: Decimal | None = None
    mean_rank_ic_1d: Decimal | None = None
    rank_ic_1d_observation_count: int = 0
    mean_ic_5d: Decimal | None = None
    mean_rank_ic_5d: Decimal | None = None
    rank_ic_5d_observation_count: int = 0
    mean_ic_20d: Decimal | None = None
    mean_rank_ic_20d: Decimal | None = None
    rank_ic_20d_observation_count: int = 0
    #: 端点（多空分位）价差均值：可交易性的直接证据。
    mean_long_short_forward_return_1d: Decimal | None = None
    mean_long_short_forward_return_5d: Decimal | None = None
    mean_long_short_forward_return_20d: Decimal | None = None
    #: 携带端点证据的天数，用于区分「没有端点证据」与「端点为负」。
    endpoint_observation_count: int = 0
    mean_quantile_monotonicity_1d: Decimal | None = None
    mean_top_quantile_turnover: Decimal | None = None
    #: 时点性状态；``unknown`` 表示未审计（fail-closed）。
    pit_status: str = "unknown"
    #: 定义生命周期状态；``retired`` 表示已被取代或已知有误（硬冻结）。
    factor_status: str = "active"
    #: Evidence-level strategy eligibility, independent from definition PIT status.
    evidence_eligibility: str = "research_only"
    evidence_eligibility_reason: str = "unverified_evidence_eligibility"

    @property
    def has_ic_evidence(self) -> bool:
        return any(
            value is not None
            for value in (
                self.mean_ic_1d,
                self.mean_rank_ic_1d,
                self.mean_ic_5d,
                self.mean_rank_ic_5d,
                self.mean_ic_20d,
                self.mean_rank_ic_20d,
            )
        )

    @property
    def has_endpoint_evidence(self) -> bool:
        """是否真的测量过 1 日端点价差（而非缺证据）。"""
        return self.endpoint_observation_count > 0

    @property
    def has_consistent_code_version(self) -> bool:
        """Whether every daily profile uses one auditable diagnostic build."""
        normalized = (self.code_version or "").strip().lower()
        return self.code_version_count == 1 and normalized not in {
            "",
            "local",
            "unknown",
        }


class FactorBatchProfilerService:
    """批量运行活跃因子的截面诊断。"""

    def __init__(
        self,
        cross_section_service: FactorCrossSectionService | None = None,
        definition_repository: FactorDefinitionRepository | None = None,
        profile_repository: FactorProfileRepository | None = None,
        job_log_repository: JobLogRepository | None = None,
    ) -> None:
        self.cross_section_service = (
            cross_section_service or FactorCrossSectionService()
        )
        self.definition_repository = (
            definition_repository or FactorDefinitionRepository()
        )
        self.profile_repository = profile_repository or FactorProfileRepository()
        self.job_log_repository = job_log_repository or JobLogRepository()
        self._summary_db: Session | None = None

    def run(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str = DEFAULT_ADJUST_TYPE,
        categories: set[str] | None = None,
        excluded_categories: set[str] | None = None,
        size_factor_name: str | None = DEFAULT_SIZE_FACTOR_NAME,
        size_factor_version: str | None = DEFAULT_SIZE_FACTOR_VERSION,
        code_version: str | None = None,
        release_id: str | None = None,
        parent_job_id: int | None = None,
    ) -> FactorBatchProfilerRunResult:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if adjust_type not in {"none", "qfq", "hfq"}:
            raise ValueError("adjust_type must be one of: none, qfq, hfq")
        if bool(size_factor_name) != bool(size_factor_version):
            raise ValueError(
                "size_factor_name and size_factor_version must be provided together"
            )
        resolved_code_version = (code_version or Settings().app_code_version).strip()
        if not resolved_code_version:
            raise ValueError("code_version must not be blank")

        definitions, _ = self.definition_repository.list(
            db,
            is_active=True,
            limit=10000,
            offset=0,
        )

        excluded = excluded_categories or set()
        candidates: list[tuple[str, str, str]] = []
        for definition in definitions:
            if categories and definition.category not in categories:
                continue
            if definition.category in excluded:
                continue
            # Skip derived cross-section transforms; they are outputs, not sources.
            if "__" in definition.name:
                continue
            candidates.append(
                (definition.name, definition.version, definition.category)
            )

        job = self.job_log_repository.create(
            db,
            job_name="factor_batch_profiler",
            job_type="schedule",
            batch_id=parent_job_id,
            metadata={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "adjust_type": adjust_type,
                "categories": sorted(categories) if categories else None,
                "excluded_categories": sorted(excluded) if excluded else None,
                "size_factor_name": size_factor_name,
                "size_factor_version": size_factor_version,
                "candidate_count": len(candidates),
            },
        )
        db.commit()

        results: list[FactorProfileResult] = []
        total_profile_rows = 0
        total_transformed_rows = 0
        success_count = 0
        failed_count = 0
        rows_read = 0

        for name, version, category in candidates:
            try:
                cs_result = self.cross_section_service.run(
                    db,
                    factor_name=name,
                    factor_version=version,
                    start_date=start_date,
                    end_date=end_date,
                    adjust_type=adjust_type,
                    code_version=resolved_code_version,
                    size_factor_name=size_factor_name,
                    size_factor_version=size_factor_version,
                    release_id=release_id,
                )
                db.commit()
                result = FactorProfileResult(
                    factor_name=name,
                    factor_version=version,
                    category=category,
                    success=True,
                    profile_rows=cs_result.profile_rows,
                    transformed_rows=cs_result.total_rows,
                    run_ids=cs_result.run_ids,
                )
                total_profile_rows += cs_result.profile_rows
                total_transformed_rows += cs_result.total_rows
                success_count += 1
            except Exception as exc:  # noqa: BLE001 - isolate per-factor failures
                db.rollback()
                result = FactorProfileResult(
                    factor_name=name,
                    factor_version=version,
                    category=category,
                    success=False,
                    error_message=f"{type(exc).__name__}: {exc}",
                )
                failed_count += 1

            rows_read += 1
            results.append(result)

        status = "success" if failed_count == 0 else "partial" if success_count > 0 else "failed"
        error_message = None
        if failed_count > 0:
            failed_names = [
                f"{r.factor_name}@{r.factor_version}"
                for r in results
                if not r.success
            ]
            error_message = f"failed factors: {', '.join(failed_names[:10])}"
            if len(failed_names) > 10:
                error_message += f" and {len(failed_names) - 10} more"

        self.job_log_repository.finish(
            db,
            job_log=job,
            status=status,
            rows_read=rows_read,
            rows_inserted=total_profile_rows + total_transformed_rows,
            rows_failed=failed_count,
            error_message=error_message,
            metadata={
                "success_count": success_count,
                "failed_count": failed_count,
                "total_profile_rows": total_profile_rows,
                "total_transformed_rows": total_transformed_rows,
                "failed_factors": [
                    {
                        "factor_name": name,
                        "factor_version": version,
                        "error_message": msg,
                    }
                    for name, version, msg in self._failed_factor_summaries(results)
                ],
            },
        )
        db.commit()

        return FactorBatchProfilerRunResult(
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            results=results,
            total_profile_rows=total_profile_rows,
            total_transformed_rows=total_transformed_rows,
            success_count=success_count,
            failed_count=failed_count,
        )

    def summarize(
        self,
        db: Session,
        *,
        factor_name: str | None = None,
        factor_version: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[FactorProfileSummary], int]:
        """聚合已持久化的逐日截面诊断为按因子汇总的 IC 证据。"""
        self._summary_db = db
        rows, total = self.profile_repository.summarize(
            db,
            factor_name=factor_name,
            factor_version=factor_version,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return [self._to_summary(row) for row in rows], total

    def _to_summary(self, row: dict[str, object]) -> FactorProfileSummary:
        category = "unknown"
        pit_status = "unknown"
        factor_status = "active"
        evidence_eligibility = str(row.get("evidence_eligibility") or "research_only")
        evidence_eligibility_reason = str(
            row.get("evidence_eligibility_reason") or "unverified_evidence_eligibility"
        )
        code_version_count = int(row.get("code_version_count") or 0)
        code_version = (
            str(row["code_version"])
            if code_version_count == 1 and row.get("code_version")
            else None
        )
        if self._summary_db is not None:
            try:
                definition = self.definition_repository.get_by_name_version(
                    self._summary_db,
                    name=str(row["factor_name"]),
                    version=str(row["factor_version"]),
                )
            except Exception:  # noqa: BLE001 - best-effort category lookup
                definition = None
            if definition is not None:
                category = definition.category
                pit_status = getattr(definition, "pit_status", None) or "unknown"
                factor_status = getattr(definition, "status", None) or "active"
        return FactorProfileSummary(
            factor_name=str(row["factor_name"]),
            factor_version=str(row["factor_version"]),
            category=category,
            trade_date_count=int(row["trade_date_count"] or 0),
            first_trade_date=row["first_trade_date"],  # type: ignore[arg-type]
            last_trade_date=row["last_trade_date"],  # type: ignore[arg-type]
            code_version=code_version,
            code_version_count=code_version_count,
            mean_coverage_rate=_to_decimal(row.get("mean_coverage_rate")),
            mean_ic_1d=_to_decimal(row.get("mean_ic_1d")),
            mean_rank_ic_1d=_to_decimal(row.get("mean_rank_ic_1d")),
            rank_ic_1d_observation_count=int(
                row.get("rank_ic_1d_observation_count") or 0
            ),
            mean_ic_5d=_to_decimal(row.get("mean_ic_5d")),
            mean_rank_ic_5d=_to_decimal(row.get("mean_rank_ic_5d")),
            rank_ic_5d_observation_count=int(
                row.get("rank_ic_5d_observation_count") or 0
            ),
            mean_ic_20d=_to_decimal(row.get("mean_ic_20d")),
            mean_rank_ic_20d=_to_decimal(row.get("mean_rank_ic_20d")),
            rank_ic_20d_observation_count=int(
                row.get("rank_ic_20d_observation_count") or 0
            ),
            mean_long_short_forward_return_1d=_to_decimal(
                row.get("mean_long_short_forward_return_1d")
            ),
            mean_long_short_forward_return_5d=_to_decimal(
                row.get("mean_long_short_forward_return_5d")
            ),
            mean_long_short_forward_return_20d=_to_decimal(
                row.get("mean_long_short_forward_return_20d")
            ),
            endpoint_observation_count=int(row.get("endpoint_observation_count") or 0),
            mean_quantile_monotonicity_1d=_to_decimal(
                row.get("mean_quantile_monotonicity_1d")
            ),
            mean_top_quantile_turnover=_to_decimal(
                row.get("mean_top_quantile_turnover")
            ),
            pit_status=pit_status,
            factor_status=factor_status,
            evidence_eligibility=evidence_eligibility,
            evidence_eligibility_reason=evidence_eligibility_reason,
        )

    @staticmethod
    def _failed_factor_summaries(
        results: list[FactorProfileResult],
    ) -> list[tuple[str, str, str | None]]:
        return [
            (r.factor_name, r.factor_version, r.error_message)
            for r in results
            if not r.success
        ]


def _to_decimal(value: object) -> Decimal | None:
    """把 SQL 聚合结果（Decimal/float/None）归一为 Decimal 或 None。"""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
