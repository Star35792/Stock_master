from __future__ import annotations

import argparse

from app.database.session import SessionLocal
from app.services import EastmoneyValuationCollectionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync Eastmoney valuation snapshots (cookie-free, bulk)."
    )
    parser.add_argument(
        "--market",
        default="all",
        choices=("all", "sh", "sz"),
        help="Market filter.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10000,
        help="Max symbols to fetch.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SessionLocal() as db:
        try:
            result = EastmoneyValuationCollectionService().collect_current(
                db,
                market=args.market,
                limit=args.limit,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    print(f"valuation.input_rows={result.input_rows}")
    print(f"valuation.normalized_rows={result.normalized_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
