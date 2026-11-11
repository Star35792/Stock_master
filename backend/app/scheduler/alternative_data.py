"""六族 alternative 数据的日级采集步骤。

为什么需要这一步
----------------
``market.alternative_data_snapshot`` 的六族（Macro / PolicyEvent /
EarningsRevision / CapitalFlow / Crowding / Sentiment）在 2026-09-18 之后
**集体断供**。排查结论不是端点失效：

* 采集服务一直存在，���点实测全活（涨停池 84 条、热股 30、异动 271、
  龙虎榜、东财主力资金 100 条都取得到）；
* ``raw.vendor_snapshot`` 里每个 alternative dataset **恰好一条**、全部停在
  2026-09-18 —— 这是「跑过一次之后再没跑过」的物证；
* ``daily_loop`` 里**根本没有 alternative / 资金流的调度入口**。

也就是说：**不是没人能取，是没人去取**。这个模块就是那个「去取」的入口。

失败语义
--------
采集依赖外部厂商网络，**瞬时故障不应该让整个日循环失败**——行情同步比它
重要得多。但也**不能静默吞掉**：吞掉就等于回到原样，断供再次伪装成成功。

因此：

* 任一源失败 → 本步骤 ``failed``，日循环标 **partial**（不是 failed），
  失败原因进 job 元数据与 reason codes；
* 任一 dataset 归一化后为空 → 本步骤 ``partial**（**空集 ≠ 端点可用**），
  空集清单进元数据；
* 全部成功且无空集 → ``success``。

无论哪种结果，六族因子都能继续按**已有历史序列**计算——快照是历史序列，
缺一天不会让整段失效，只会让最新一天陈旧。这正是把它做成 partial 而不是
硬失败的理由。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.repositories.system import JobLogRepository
from app.services.eastmoney_collection import EastmoneyFlowCollectionService
from app.services.hithink_alternative_collection import (
    HithinkAlternativeCollectionService,
)
from app.services.job_logging import logged_job

logger = logging.getLogger(__name__)

#: 采集源。``hithink`` = 同花顺情绪族 + 指数目录；``eastmoney`` = 东财资金流族。
DEFAULT_SOURCES = ("hithink", "eastmoney")

#: 本步骤在 job_log 里的任务名。
JOB_NAME = "alternative_data_collection"


@dataclass
class AlternativeDataCollectionResult:
    """六族采集步骤的结果。"""

    #: ``success`` / ``partial`` / ``failed``
    status: str = "success"
    #: 每个源的批次号与入库行数。
    sources: dict[str, dict[str, object]] = field(default_factory=dict)
    #: 归一化后为空的 dataset（跨源合并）。
    empty_datasets: tuple[str, ...] = ()
    #: 失败的源与原因。
    failures: dict[str, str] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def partial(self) -> bool:
        return self.status == "partial"


def run_alternative_data_collection(
    *,
    sources: tuple[str, ...] = DEFAULT_SOURCES,
    session_factory: Callable[[], Session] = SessionLocal,
    job_log_repository: JobLogRepository | None = None,
) -> AlternativeDataCollectionResult:
    """采集六族 alternative 数据并登记一次任务记录。

    每个源**独立**执行：一个源挂掉不影响另一个源的结果。
    """

    result = AlternativeDataCollectionResult()
    empty: list[str] = []

    with session_factory() as db:
        with logged_job(
            db,
            job_name=JOB_NAME,
            job_type="schedule",
            metadata={"sources": list(sources)},
            repository=job_log_repository,
        ) as job:
            for source in sources:
                try:
                    with session_factory() as source_db:
                        if source == "hithink":
                            collected = HithinkAlternativeCollectionService().collect_current(
                                source_db
                            )
                            source_db.commit()
                        elif source == "eastmoney":
                            collected = EastmoneyFlowCollectionService().collect_current(
                                source_db
                            )
                            source_db.commit()
                        else:
                            raise ValueError(f"unknown alternative data source: {source}")
                except Exception as exc:  # noqa: BLE001 - 单源失败不拖垮其他源
                    message = f"{type(exc).__name__}: {exc}"
                    result.failures[source] = message
                    logger.warning("alternative data source %s failed: %s", source, message)
                    continue

                result.sources[source] = {
                    "batch_no": collected.batch_no,
                    "raw_snapshots": collected.raw_snapshots,
                    "normalized_rows": collected.normalized_rows,
                }
                empty.extend(collected.empty_datasets)

            result.empty_datasets = tuple(sorted(set(empty)))

            if result.failures:
                result.status = "failed"
            elif result.empty_datasets:
                result.status = "partial"

            job.status = result.status
            job.rows_inserted = sum(
                int(source.get("normalized_rows") or 0)
                for source in result.sources.values()
            )
            job.metadata_ = {
                **(job.metadata_ or {}),
                "sources": result.sources,
                "empty_datasets": list(result.empty_datasets),
                "failures": result.failures,
                "status": result.status,
            }
            db.commit()

    return result
