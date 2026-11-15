from __future__ import annotations

import argparse
import logging

from app.database.session import SessionLocal, initialize_database
from app.scheduler import create_daily_sync_scheduler
from app.services.job_lease import recover_abandoned_jobs


def _parse_symbols(value: str) -> list[str]:
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the scheduled HiThink daily sync.")
    parser.add_argument(
        "--symbols",
        required=True,
        help="Comma-separated symbols, for example 600519.SH,000001.SZ",
    )
    parser.add_argument("--hour", type=int, default=18, help="Daily run hour.")
    parser.add_argument("--minute", type=int, default=10, help="Daily run minute.")
    parser.add_argument("--timezone", default="Asia/Shanghai", help="Scheduler timezone.")
    parser.add_argument("--days", type=int, default=14, help="Daily lookback window.")
    parser.add_argument(
        "--adjust-type",
        default="none",
        choices=("none", "qfq", "hfq"),
        help="Adjustment type to sync.",
    )
    parser.add_argument(
        "--refresh-reference-data",
        action="store_true",
        help="Refresh reference data before every scheduled sync.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    initialize_database()
    recover_abandoned_jobs(SessionLocal)
    scheduler = create_daily_sync_scheduler(
        symbols=_parse_symbols(args.symbols),
        hour=args.hour,
        minute=args.minute,
        timezone=args.timezone,
        days=args.days,
        adjust_type=args.adjust_type,
        refresh_reference_data=args.refresh_reference_data,
    )
    logging.getLogger(__name__).info(
        "Starting HiThink scheduler: weekdays at %02d:%02d %s",
        args.hour,
        args.minute,
        args.timezone,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
