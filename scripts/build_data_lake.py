from __future__ import annotations

import argparse

from app.lake import DataLake


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the local partitioned Parquet lake.")
    parser.add_argument("--daily", default="data/hithink_dumps/daily-k.parquet")
    parser.add_argument(
        "--actions",
        default="data/hithink_dumps/adjustment-factors.parquet",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    lake = DataLake()
    daily = lake.import_daily_bars(args.daily)
    actions = lake.import_corporate_actions(args.actions)
    print(f"daily.release={daily.release} rows={daily.row_count} path={daily.path}")
    print(f"actions.release={actions.release} rows={actions.row_count} path={actions.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
