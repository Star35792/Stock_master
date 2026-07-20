from __future__ import annotations

import argparse

from app.database.session import SessionLocal
from app.services import HithinkValuationCollectionService


def _parse_symbols(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync HiThink valuation snapshots.")
    parser.add_argument("--symbols", help="Optional comma-separated symbols.")
    parser.add_argument("--all-market", action="store_true")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--offset", type=int, default=0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SessionLocal() as db:
        try:
            result = HithinkValuationCollectionService().collect_current(
                db,
                symbols=_parse_symbols(args.symbols),
                fetch_all_market=args.all_market,
                limit=args.limit,
                offset=args.offset,
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
