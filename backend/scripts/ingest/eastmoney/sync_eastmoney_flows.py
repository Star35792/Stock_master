from __future__ import annotations

import argparse

from app.database.session import SessionLocal
from app.services.eastmoney_collection import EastmoneyFlowCollectionService


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture Eastmoney capital-flow snapshots.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--market", choices=("all", "sh", "sz", "cyb", "kcb"), default="all")
    args = parser.parse_args()
    with SessionLocal() as db:
        result = EastmoneyFlowCollectionService().collect_current(
            db, limit=args.limit, market=args.market
        )
    print(f"batch={result.batch_no}")
    print(f"raw_snapshots={result.raw_snapshots}")
    print(f"normalized_rows={result.normalized_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
