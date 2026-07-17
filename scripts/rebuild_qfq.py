import argparse
from datetime import date, timedelta

from app.database.session import SessionLocal
from app.services.adjustment_build import AdjustmentBuildService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild qfq factors and daily bars.")
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        default=date.today() - timedelta(days=400),
        help="First qfq bar date, YYYY-MM-DD; factors are still rebuilt for all history.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SessionLocal() as db:
        result = AdjustmentBuildService().rebuild_qfq(db, qfq_start_date=args.start_date)
    print(f"factor_rows={result.factor_rows}")
    print(f"qfq_bar_rows={result.qfq_bar_rows}")
    print(f"version={result.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
