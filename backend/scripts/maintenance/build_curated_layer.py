from __future__ import annotations

import argparse

from app.lake import CuratedLayer, DataLake


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build curated analysis datasets from the raw data lake."
    )
    parser.add_argument(
        "dataset",
        choices=["prices_qfq", "fundamentals_pit"],
        help="Which curated dataset to materialize.",
    )
    parser.add_argument(
        "--source",
        help="Parquet file/glob of raw financial statements (required for fundamentals_pit).",
    )
    args = parser.parse_args()

    layer = CuratedLayer(DataLake())
    if args.dataset == "prices_qfq":
        result = layer.build_prices_qfq()
    else:
        if not args.source:
            parser.error("fundamentals_pit requires --source")
        result = layer.build_fundamentals_pit(args.source)

    print(f"dataset={result.dataset}")
    print(f"path={result.path}")
    print(f"row_count={result.row_count}")
    print(f"min_date={result.min_date}")
    print(f"max_date={result.max_date}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
