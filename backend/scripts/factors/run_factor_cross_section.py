from __future__ import annotations

import argparse
from datetime import date

from app.database.session import SessionLocal
from app.services import FactorCrossSectionService


def _parse_symbols(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run cross-sectional transforms and profile a factor."
    )
    parser.add_argument("--factor-name", required=True)
    parser.add_argument("--factor-version", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--symbols", help="Optional comma-separated symbols.")
    parser.add_argument(
        "--adjust-type",
        required=True,
        choices=("none", "qfq", "hfq"),
    )
    parser.add_argument("--size-factor-name")
    parser.add_argument("--size-factor-version")
    parser.add_argument("--code-version", default="local")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SessionLocal() as db:
        try:
            result = FactorCrossSectionService().run(
                db,
                factor_name=args.factor_name,
                factor_version=args.factor_version,
                start_date=date.fromisoformat(args.start_date),
                end_date=date.fromisoformat(args.end_date),
                adjust_type=args.adjust_type,
                code_version=args.code_version,
                symbols=_parse_symbols(args.symbols),
                size_factor_name=args.size_factor_name,
                size_factor_version=args.size_factor_version,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    print(f"factor.source={result.source_factor_name}")
    print(f"factor.source_version={result.source_factor_version}")
    print(f"factor.profile_rows={result.profile_rows}")
    print(f"factor.transformed_rows={result.total_rows}")
    for transform_name, rows_written in result.rows_by_transform.items():
        print(f"factor.{transform_name}.rows={rows_written}")
        print(f"factor.{transform_name}.run_id={result.run_ids[transform_name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
