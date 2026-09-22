from __future__ import annotations

import argparse

from app.database.session import SessionLocal
from app.services.hithink_dump_collection import HithinkDumpCollectionService
from app.services.reference_data import ReferenceDataSyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync HiThink market dumps into raw and market.")
    parser.add_argument("--full", action="store_true", help="Import the full 10-year dump.")
    parser.add_argument(
        "--skip-reference-data",
        action="store_true",
        help="Do not refresh securities and trading calendar first.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SessionLocal() as db:
        if not args.skip_reference_data:
            ReferenceDataSyncService().sync_hithink_reference_data(db)
        result = HithinkDumpCollectionService().collect(db, full=args.full)
        adjustment_result = HithinkDumpCollectionService().collect_adjustments(db)
        market_rows = HithinkDumpCollectionService().build_market_daily(
            db,
            batch_no=result.batch_no,
        )
    print(f"batch={result.batch_no}")
    print(f"release={result.release_tag}")
    print(f"rows={result.row_count}")
    print(f"accepted_rows={result.accepted_count}")
    print(f"skipped_rows={result.skipped_count}")
    print(f"window={result.start_date}..{result.end_date}")
    print(f"adjustment_release={adjustment_result.release_tag}")
    print(f"adjustment_rows={adjustment_result.row_count}")
    print(f"market_rows={market_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
