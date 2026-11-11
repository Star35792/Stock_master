"""端到端采集 ``market.alternative_data_snapshot``，并给出供给体检报告。

这个入口补上的是断供的**主因**：采集服务一直存在且端点一直可用，但没有任何
调度入口调用它——``raw.vendor_snapshot`` 里每个 alternative dataset 恰好一条、
全部停在 2026-09-18，就是「跑过一次之后再没跑过」的物证。

用法::

    cd backend && python -m scripts.ingest.collect_alternative_data
    cd backend && python -m scripts.ingest.collect_alternative_data --source hithink
    cd backend && python -m scripts.ingest.collect_alternative_data --report-only
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.settings import settings  # noqa: E402
from app.database.session import engine  # noqa: E402
from app.models.market import AlternativeDataSnapshot  # noqa: E402
from app.services.alternative_data_supply import (  # noqa: E402
    DATASET_SUPPLY_REGISTRY,
    SupplyStatus,
)
from app.services.eastmoney_collection import EastmoneyFlowCollectionService  # noqa: E402
from app.services.hithink_alternative_collection import (  # noqa: E402
    HithinkAlternativeCollectionService,
)


@dataclass(frozen=True)
class DatasetCoverage:
    """单个 dataset 在表内的实际覆盖情况。"""

    dataset: str
    rows: int
    last_snapshot_date: str | None
    registered: bool
    status: SupplyStatus
    family: str | None
    blocker: str | None


def _coverage(db: Session) -> list[DatasetCoverage]:
    rows = db.execute(
        select(
            AlternativeDataSnapshot.dataset,
            AlternativeDataSnapshot.snapshot_date,
        )
    ).all()
    totals: dict[str, int] = {}
    latest: dict[str, str] = {}
    for dataset, snapshot_date in rows:
        totals[dataset] = totals.get(dataset, 0) + 1
        key = snapshot_date.isoformat()
        if dataset not in latest or key > latest[dataset]:
            latest[dataset] = key
    coverage: list[DatasetCoverage] = []
    for dataset, spec in DATASET_SUPPLY_REGISTRY.items():
        coverage.append(
            DatasetCoverage(
                dataset=dataset,
                rows=totals.get(dataset, 0),
                last_snapshot_date=latest.get(dataset),
                registered=True,
                status=spec.status,
                family=spec.factor_family,
                blocker=spec.blocker,
            )
        )
    for dataset in sorted(set(totals) - set(DATASET_SUPPLY_REGISTRY)):
        coverage.append(
            DatasetCoverage(
                dataset=dataset,
                rows=totals[dataset],
                last_snapshot_date=latest.get(dataset),
                registered=False,
                status=SupplyStatus.NOT_CONNECTED,
                family=None,
                blocker="未登记在供给台账中",
            )
        )
    return sorted(coverage, key=lambda item: (item.family or "zzz", item.dataset))


def _print_report(coverage: list[DatasetCoverage]) -> None:
    print("\n=== 供给体检 ===")
    print(f"{'dataset':32} {'rows':>7}  {'最新快照':12} {'族':16} 状态")
    for item in coverage:
        family = item.family or "-"
        print(
            f"{item.dataset:32} {item.rows:>7}  "
            f"{(item.last_snapshot_date or '-'):12} {family:16} {item.status.value}"
        )
    not_connected = [item for item in coverage if item.status is SupplyStatus.NOT_CONNECTED]
    blocked = [item for item in coverage if item.status is SupplyStatus.BLOCKED]
    empty = [item for item in coverage if item.status is SupplyStatus.EMPTY]
    if not_connected:
        print("\n未接通（因子声明了依赖但无采集实现）:")
        for item in not_connected:
            print(f"  {item.dataset:32} {item.blocker}")
    if blocked:
        print("\n上游断供（补不了）:")
        for item in blocked:
            print(f"  {item.dataset:32} {item.blocker}")
    if empty:
        print("\n空集（端点可用但归一化后无行）:")
        for item in empty:
            print(f"  {item.dataset:32} {item.blocker}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect alternative_data_snapshot and report supply coverage",
    )
    parser.add_argument(
        "--source",
        choices=("hithink", "eastmoney", "all"),
        default="all",
        help="which collector to run",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="only print the supply report; do not hit the network",
    )
    parser.add_argument("--db", help="override the control-plane database path")
    args = parser.parse_args()

    db_path = Path(args.db or settings.metadata_db_path)
    print("=== alternative_data_snapshot 采集 ===")
    print(f"数据库: {db_path}")
    if args.source != "eastmoney":
        print("采集同花顺情绪族 + 指数目录")
    if args.source != "hithink":
        print("采集东财资金流族")

    if args.db:
        from sqlalchemy import create_engine

        bind = create_engine(f"duckdb:///{db_path}")
    else:
        bind = engine

    with Session(bind) as db:
        if not args.report_only:
            if args.source != "eastmoney":
                hithink_result = HithinkAlternativeCollectionService().collect_current(db)
                print(
                    f"\n同花顺: 批次={hithink_result.batch_no} "
                    f"raw={hithink_result.raw_snapshots} "
                    f"入库={hithink_result.normalized_rows} "
                    f"空集={list(hithink_result.empty_datasets) or '无'}"
                )
            if args.source != "hithink":
                eastmoney_result = EastmoneyFlowCollectionService().collect_current(db)
                print(
                    f"东财  : 批次={eastmoney_result.batch_no} "
                    f"raw={eastmoney_result.raw_snapshots} "
                    f"入库={eastmoney_result.normalized_rows} "
                    f"空集={list(eastmoney_result.empty_datasets) or '无'}"
                )
        _print_report(_coverage(db))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
