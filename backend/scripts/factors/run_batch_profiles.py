from __future__ import annotations

import argparse
from datetime import date

from app.database.session import SessionLocal
from app.services import FactorBatchProfilerService


def _parse_csv(value: str | None) -> set[str] | None:
    if value is None:
        return None
    items = {item.strip() for item in value.split(",") if item.strip()}
    return items or None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Batch-run cross-section diagnostics (IC/rank IC, quantile returns, "
            "transforms) for all active factor definitions."
        )
    )
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument(
        "--adjust-type",
        default="qfq",
        choices=("none", "qfq", "hfq"),
    )
    parser.add_argument(
        "--categories",
        help="Optional comma-separated category whitelist.",
    )
    parser.add_argument(
        "--excluded-categories",
        help="Optional comma-separated category blocklist.",
    )
    parser.add_argument("--size-factor-name", default="market_cap")
    parser.add_argument("--size-factor-version", default="v1-qfq")
    parser.add_argument(
        "--no-size-neutral",
        action="store_true",
        help="Skip industry_size_neutral transforms (drops the size factor).",
    )
    parser.add_argument("--code-version", default="local")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    size_factor_name = None if args.no_size_neutral else args.size_factor_name
    size_factor_version = None if args.no_size_neutral else args.size_factor_version

    with SessionLocal() as db:
        result = FactorBatchProfilerService().run(
            db,
            start_date=date.fromisoformat(args.start_date),
            end_date=date.fromisoformat(args.end_date),
            adjust_type=args.adjust_type,
            categories=_parse_csv(args.categories),
            excluded_categories=_parse_csv(args.excluded_categories),
            size_factor_name=size_factor_name,
            size_factor_version=size_factor_version,
            code_version=args.code_version,
        )

    print(f"batch.start_date={result.start_date}")
    print(f"batch.end_date={result.end_date}")
    print(f"batch.adjust_type={result.adjust_type}")
    print(f"batch.success_count={result.success_count}")
    print(f"batch.failed_count={result.failed_count}")
    print(f"batch.total_profile_rows={result.total_profile_rows}")
    print(f"batch.total_transformed_rows={result.total_transformed_rows}")
    for item in result.results:
        if item.success:
            print(
                f"factor.ok name={item.factor_name} version={item.factor_version} "
                f"category={item.category} profile_rows={item.profile_rows} "
                f"transformed_rows={item.transformed_rows}"
            )
        else:
            print(
                f"factor.failed name={item.factor_name} version={item.factor_version} "
                f"category={item.category} error={item.error_message}"
            )
    return 1 if result.failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
