"""六族 alternative 数据历史回填。

用法::

    python -m scripts.ingest.backfill_alternative_history --start 2026-08-01 --end 2026-11-10
    python -m scripts.ingest.backfill_alternative_history --start 2026-08-01 --end 2026-11-10 \\
        --datasets hot_stock limit_up_pool

脚本会打印每个 dataset 补到的快照日数与行数，便于核对「历史到底有没有补上」。
"""

from __future__ import annotations

import argparse
from datetime import date

from app.database.session import SessionLocal
from app.services.hithink_alternative_history import (
    DEFAULT_HISTORY_DATASETS,
    HithinkAlternativeHistoryService,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill historical snapshots for the six-family alternative datasets."
    )
    parser.add_argument("--start", required=True, help="起始交易日 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束交易日 YYYY-MM-DD")
    parser.add_argument(
        "--datasets",
        nargs="+",
        default=list(DEFAULT_HISTORY_DATASETS),
        help="要回填的 dataset 名（默认全部）",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.35,
        help="每次请求之间的间隔秒数（默认 0.35，避免触发限流）",
    )
    return parser


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    args = build_parser().parse_args()
    start_date = _parse_date(args.start)
    end_date = _parse_date(args.end)

    service = HithinkAlternativeHistoryService(request_interval_seconds=args.interval)
    with SessionLocal() as db:
        result = service.collect_history(
            db,
            start_date=start_date,
            end_date=end_date,
            datasets=tuple(args.datasets),
        )

    print(f"批次        : {result.batch_no}")
    print(f"交易日      : {result.trade_days}")
    print(f"原始快照    : {result.raw_snapshots}")
    print(f"归一化行数  : {result.normalized_rows}")
    print(f"状态        : {result.status}")
    if result.empty_datasets:
        print(f"空集        : {len(result.empty_datasets)}")
        for item in result.empty_datasets[:20]:
            print(f"    {item}")
    if result.failures:
        print(f"失败        : {len(result.failures)}")
        for key, message in list(result.failures.items())[:20]:
            print(f"    {key}: {message}")
    return 0 if result.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
