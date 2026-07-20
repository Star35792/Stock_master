from __future__ import annotations

import argparse

from app.data_sources import HithinkFinanceDataSource
from app.database.session import SessionLocal
from app.lake import DataLake
from app.services.hithink_alternative_collection import HithinkAlternativeCollectionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync HiThink alternative A-share data.")
    parser.add_argument(
        "--indices",
        default="000300.SH,000905.SH,000852.SH",
        help="Comma-separated index symbols whose current constituents are captured.",
    )
    parser.add_argument("--lake-only", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    indices = [item.strip().upper() for item in args.indices.split(",") if item.strip()]
    if args.lake_only:
        source = HithinkFinanceDataSource()
        lake = DataLake()
        payloads = {
            "limit_up_pool": source.fetch_limit_up_pool(),
            "limit_up_ladder": source.fetch_limit_up_ladder(),
            "anomaly": source.fetch_anomaly_list(),
            "hot_stock": source.fetch_hot_stock_list(),
            "skyrocket": source.fetch_skyrocket_list(),
            "dragon_tiger": source.fetch_dragon_tiger_list(),
        }
        for dataset, payload in payloads.items():
            lake.append_payloads(dataset, [payload])
        for index_symbol in indices:
            lake.append_payloads(
                "index_constituents",
                [
                    {"index_symbol": index_symbol, **member}
                    for member in source.fetch_index_constituents(symbol=index_symbol)
                ],
            )
        return 0
    with SessionLocal() as db:
        result = HithinkAlternativeCollectionService().collect_current(
            db,
            index_symbols=indices,
        )
    print(f"raw_snapshots={result.raw_snapshots}")
    print(f"normalized_rows={result.normalized_rows}")
    print(f"constituent_rows={result.constituent_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
