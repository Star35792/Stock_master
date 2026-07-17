import argparse
from datetime import date

from app.lake import DataLake, LakeFactorPipeline


def _dates(value: str | None) -> list[date] | None:
    if not value:
        return None
    return [date.fromisoformat(item.strip()) for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build qfq price-volume factors in Parquet.")
    parser.add_argument("--release", required=True)
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--symbols", help="Comma-separated symbols; defaults to all symbols.")
    parser.add_argument("--rebalance-dates", help="Comma-separated dates to retain.")
    args = parser.parse_args()

    symbols = [item.strip() for item in args.symbols.split(",")] if args.symbols else None
    result = LakeFactorPipeline(DataLake()).build_factors(
        release=args.release,
        start_date=args.start_date,
        end_date=args.end_date,
        symbols=symbols,
        rebalance_dates=_dates(args.rebalance_dates),
    )
    print(f"factor.path={result.path}")
    print(f"factor.rows={result.row_count}")


if __name__ == "__main__":
    main()
