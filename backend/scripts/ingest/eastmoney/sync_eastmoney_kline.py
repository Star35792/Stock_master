from __future__ import annotations

import argparse
from datetime import date

from app.data_sources import EastmoneyDataSource
from app.database.session import SessionLocal
from app.services.daily_bar_market_builder import DailyBarMarketBuildService
from app.services.hithink_collection import HithinkDailyBarCollectionService


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture Eastmoney forward-adjusted daily K-lines."
    )
    parser.add_argument(
        "--symbols", required=True, help="Comma-separated six-digit codes or thscodes."
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(1990, 1, 1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--build-market", action="store_true")
    args = parser.parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    if not symbols:
        raise ValueError("symbols must not be empty")
    with SessionLocal() as db:
        collection = HithinkDailyBarCollectionService(data_source=EastmoneyDataSource())
        result = collection.collect_daily_bars(
            db,
            symbols=symbols,
            start_date=args.start_date,
            end_date=args.end_date,
            adjust_type="qfq",
        )
        print(f"batch={result.batch_no}")
        print(f"raw_rows={result.accepted_count}")
        if args.build_market and result.accepted_count:
            market = DailyBarMarketBuildService().build_from_raw(
                db,
                symbols=symbols,
                start_date=args.start_date,
                end_date=args.end_date,
                adjust_type="qfq",
                source_name="eastmoney",
            )
            print(f"market_trusted_rows={market.trusted_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
