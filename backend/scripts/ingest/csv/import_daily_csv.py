from __future__ import annotations

import argparse
from pathlib import Path

from app.database.session import SessionLocal
from app.services import CsvDailyBarCollectionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import daily bars from CSV into raw storage.")
    parser.add_argument("file_path", help="CSV file path.")
    parser.add_argument("--source-name", default="csv", help="Source name recorded in raw tables.")
    parser.add_argument("--encoding", help="CSV encoding. Auto-detected when omitted.")
    parser.add_argument(
        "--source-priority",
        type=int,
        default=40,
        help="Smaller numbers have higher source priority.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SessionLocal() as db:
        result = CsvDailyBarCollectionService().collect_daily_bars(
            db,
            file_path=Path(args.file_path),
            source_name=args.source_name,
            encoding=args.encoding,
            source_priority=args.source_priority,
        )

    print(f"collection.batch_no={result.batch_no}")
    print(f"collection.source_name={result.source_name}")
    print(f"collection.status={result.status}")
    print(f"collection.accepted={result.accepted_count}")
    print(f"collection.rejected={result.rejected_count}")
    if result.error_message:
        print(f"collection.error={result.error_message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
