from __future__ import annotations

import argparse
from datetime import date

from app.database.session import SessionLocal
from app.services import HithinkDailySyncService


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _parse_symbols(value: str) -> list[str]:
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync HiThink daily bars into raw and market.")
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated symbols, for example 600519.SH,000001.SZ",
    )
    parser.add_argument("--start-date", help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", help="Inclusive end date, YYYY-MM-DD.")
    parser.add_argument(
        "--days",
        type=int,
        default=14,
        help="Lookback days when --start-date is omitted.",
    )
    parser.add_argument(
        "--adjust-type",
        default="none",
        choices=("none", "qfq", "hfq"),
        help="Adjustment type to sync.",
    )
    parser.add_argument(
        "--refresh-reference-data",
        action="store_true",
        help="Refresh market.security and market.trading_calendar before syncing bars.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SessionLocal() as db:
        result = HithinkDailySyncService().sync(
            db,
            symbols=_parse_symbols(args.symbols),
            start_date=_parse_date(args.start_date),
            end_date=_parse_date(args.end_date),
            days=args.days,
            adjust_type=args.adjust_type,
            refresh_reference_data=args.refresh_reference_data,
        )

    if result.reference_data:
        print(f"reference.securities={result.reference_data.securities_count}")
        print(f"reference.trading_days={result.reference_data.trading_days_count}")
    print(f"collection.batch_no={result.collection.batch_no}")
    print(f"collection.status={result.collection.status}")
    print(f"collection.accepted={result.collection.accepted_count}")
    print(f"collection.rejected={result.collection.rejected_count}")
    if result.collection.error_message:
        print(f"collection.error={result.collection.error_message}")
    print(f"market.candidates={result.market_build.candidates_count}")
    print(f"market.trusted={result.market_build.trusted_count}")
    print(f"market.rejected={result.market_build.rejected_count}")
    print(f"market.issues={result.market_build.issue_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
