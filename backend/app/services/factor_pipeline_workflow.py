"""因子批量计算管道与日级调度。

把九个因子服务（价量、财务、估值、行业、情绪、宏观、政策、盈利预期、资金结构）
和 crowding 复合因子按统一接口批量计算，每个服务独立事务，互不回滚。
因子值通过 ``upsert_value`` 按 (symbol, trade_date, factor_name, factor_version)
幂等写入，因此重复计算或滚动窗口重算不会产生重复行，适合日级增量。

``composite``（``decision_score``）依赖调用方传入的因子清单，属运行时动态组合，
不在此批量管道内计算。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.session import SessionLocal
from app.lake import CuratedDailyBarReader
from app.repositories.system import JobLogRepository
from app.services import (
    BasicPriceVolumeFactorService,
    CapitalFlowFactorService,
    CrowdingFactorService,
    EarningsRevisionFactorService,
    FinancialFactorService,
    IndustryFactorService,
    MacroFactorService,
    PolicyEventFactorService,
    SentimentFactorService,
    ValuationFactorService,
)
from app.services.basic_price_volume_factors import BASIC_FACTOR_SPECS
from app.services.job_logging import logged_job

logger = logging.getLogger(__name__)


@dataclass
class FactorBatchResult:
    start_date: date
    end_date: date
    adjust_type: str
    services: dict[str, dict] = field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        return sum(svc.get("total_rows", 0) for svc in self.services.values())

    @property
    def failed(self) -> list[str]:
        return [name for name, svc in self.services.items() if svc.get("status") == "failed"]

    @property
    def empty(self) -> list[str]:
        """Services that completed normally but produced no observations."""
        return [name for name, svc in self.services.items() if svc.get("status") == "empty"]


# (service_class, takes_factor_names)
_FACTOR_SERVICES: list[tuple[type, bool]] = [
    (BasicPriceVolumeFactorService, True),
    (FinancialFactorService, True),
    (ValuationFactorService, True),
    (IndustryFactorService, True),
    (SentimentFactorService, True),
    (MacroFactorService, True),
    (PolicyEventFactorService, True),
    (EarningsRevisionFactorService, True),
    (CapitalFlowFactorService, True),
    (CrowdingFactorService, False),
]

# Daily collection computes every price/volume factor supported by the trusted
# curated bars. Turnover history is currently absent from that dataset.
DAILY_PRICE_VOLUME_FACTORS = tuple(
    spec.name
    for spec in BASIC_FACTOR_SPECS
    if spec.name not in {"turnover_rate", "turnover_percentile_120"}
)
DAILY_FACTOR_SET_VERSION = "price-volume-16-v1"


def run_all_factors(
    *,
    start_date: date,
    end_date: date,
    symbols: list[str] | None = None,
    adjust_type: str = "qfq",
    code_version: str = "local",
    release_id: str | None = None,
    curated_path: str | Path | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
    job_log_repository: JobLogRepository | None = None,
) -> FactorBatchResult:
    """批量计算全部因子服务。每服务独立事务，单服务失败不影响其他。"""
    if start_date > end_date:
        raise ValueError("start_date must be <= end_date")

    result = FactorBatchResult(
        start_date=start_date,
        end_date=end_date,
        adjust_type=adjust_type,
    )
    with session_factory() as db:
        with logged_job(
            db,
            job_name="factor_pipeline",
            job_type="schedule",
            metadata={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "adjust_type": adjust_type,
                "symbols_count": len(symbols) if symbols is not None else None,
                "code_version": code_version,
                "release_id": release_id,
                "curated_path": str(curated_path) if curated_path is not None else None,
            },
            repository=job_log_repository,
        ) as job:
            # Serve qfq bars from the curated Parquet layer when enabled. Only
            # qfq is materialized; any other adjust_type keeps the ORM path.
            curated_reader = (
                CuratedDailyBarReader(dataset_path=curated_path)
                if settings.factor_reads_curated and adjust_type == "qfq"
                else None
            )
            for service_cls, takes_factor_names in _FACTOR_SERVICES:
                name = service_cls.__name__
                with session_factory() as db2:
                    try:
                        kwargs: dict = {
                            "start_date": start_date,
                            "end_date": end_date,
                            "symbols": symbols,
                            "adjust_type": adjust_type,
                            "code_version": code_version,
                            "release_id": release_id,
                        }
                        if takes_factor_names:
                            kwargs["factor_names"] = (
                                list(DAILY_PRICE_VOLUME_FACTORS)
                                if service_cls is BasicPriceVolumeFactorService
                                else None
                            )
                        service_kwargs = (
                            {"daily_bar_repository": curated_reader}
                            if curated_reader is not None
                            else {}
                        )
                        service_result = service_cls(**service_kwargs).run(db2, **kwargs)
                        db2.commit()
                        total_rows = getattr(service_result, "total_rows", 0)
                        result.services[name] = {
                            # Zero rows is not a failed transaction, but it is
                            # not evidence that a factor family is available.
                            "status": "success" if total_rows > 0 else "empty",
                            "total_rows": total_rows,
                            "input_rows": getattr(service_result, "input_rows", 0),
                            "run_ids": list(getattr(service_result, "run_ids", {}).values()),
                        }
                        logger.info(
                            "%s ok: total_rows=%s", name, result.services[name]["total_rows"]
                        )
                    except Exception as exc:  # noqa: BLE001 - per-service isolation
                        db2.rollback()
                        logger.exception("%s failed", name)
                        result.services[name] = {
                            "status": "failed",
                            "error": str(exc),
                            "total_rows": 0,
                        }
            job.rows_inserted = result.total_rows
            job.rows_failed = len(result.failed)
            if result.failed or result.empty:
                job.status = "partial"
            job.metadata_ = {
                **(job.metadata_ or {}),
                "failed_services": result.failed,
                "empty_services": result.empty,
                "services": {
                    name: svc.get("total_rows", 0) for name, svc in result.services.items()
                },
            }

    logger.info(
        "factor batch done: total_rows=%s failed=%s",
        result.total_rows,
        result.failed,
    )
    return result
