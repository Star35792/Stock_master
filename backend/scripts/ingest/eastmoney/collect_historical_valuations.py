"""Collect a supplier-dated valuation snapshot into raw evidence only."""

from __future__ import annotations

import argparse
import json
from datetime import date

from app.database.session import SessionLocal, initialize_database
from app.services.price_value_historical_collection import (
    PriceValueHistoricalCollectionService,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-date", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    initialize_database()
    with SessionLocal() as db:
        result = PriceValueHistoricalCollectionService().collect(
            db, trade_date=args.trade_date
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
