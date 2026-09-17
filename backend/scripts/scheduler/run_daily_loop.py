from __future__ import annotations

import argparse
import logging
import sys

from app.database.session import SessionLocal, initialize_database
from app.scheduler.daily_loop import run_daily_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def _parse_symbols(value: str | None) -> list[str] | None:
    if value is None:
        return None
    symbols = [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]
    return symbols if symbols else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one daily closed loop: sync market data then batch-compute factors."
    )
    parser.add_argument(
        "--symbols",
        help="Optional comma-separated symbols; omit to use all active securities.",
    )
    parser.add_argument(
        "--sync-days",
        type=int,
        default=14,
        help="Number of days to sync from HiThink.",
    )
    parser.add_argument(
        "--factor-lookback-days",
        type=int,
        default=14,
        help="Lookback window for factor recomputation.",
    )
    parser.add_argument(
        "--sync-adjust-type",
        default="none",
        choices=("none", "qfq", "hfq"),
        help="Adjustment type for raw daily bar sync.",
    )
    parser.add_argument(
        "--factor-adjust-type",
        default="qfq",
        choices=("none", "qfq", "hfq"),
        help="Adjustment type for factor computation.",
    )
    parser.add_argument(
        "--refresh-reference-data",
        action="store_true",
        help="Refresh securities and trading calendar before sync.",
    )
    parser.add_argument(
        "--code-version",
        default="local",
        help="Code version recorded in factor runs.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    initialize_database()
    result = run_daily_loop(
        symbols=_parse_symbols(args.symbols),
        sync_days=args.sync_days,
        factor_lookback_days=args.factor_lookback_days,
        sync_adjust_type=args.sync_adjust_type,
        factor_adjust_type=args.factor_adjust_type,
        refresh_reference_data=args.refresh_reference_data,
        code_version=args.code_version,
        session_factory=SessionLocal,
    )
    print(f"daily_loop.failed={result.failed}")
    print(f"daily_loop.skipped={result.skipped}")
    print(f"daily_loop.skip_reason={result.skip_reason or ''}")
    print(f"daily_loop.sync_error={result.sync_error or ''}")
    print(f"daily_loop.factor_error={result.factor_error or ''}")
    if result.sync_result:
        print(f"daily_loop.sync_trusted={result.sync_result.market_build.trusted_count}")
    if result.factor_result:
        print(f"daily_loop.factor_total_rows={result.factor_result.total_rows}")
        print(f"daily_loop.factor_failed_services={','.join(result.factor_result.failed)}")
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
