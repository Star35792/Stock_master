from __future__ import annotations

import argparse
from datetime import date

from app.database.session import SessionLocal
from app.services import SentimentFactorService


def _parse_symbols(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


def _parse_factor_names(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [name.strip() for name in value.split(",") if name.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate trusted market sentiment factors.")
    parser.add_argument("--start-date", required=True, help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Inclusive end date, YYYY-MM-DD.")
    parser.add_argument("--symbols", help="Optional comma-separated symbols.")
    parser.add_argument("--factors", help="Optional comma-separated factor names.")
    parser.add_argument(
        "--adjust-type",
        default="qfq",
        choices=("none", "qfq", "hfq"),
        help="Daily bar adjustment type used for the target trading universe.",
    )
    parser.add_argument(
        "--code-version",
        default="local",
        help="Code version recorded in factor runs.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SessionLocal() as db:
        try:
            result = SentimentFactorService().run(
                db,
                start_date=date.fromisoformat(args.start_date),
                end_date=date.fromisoformat(args.end_date),
                symbols=_parse_symbols(args.symbols),
                factor_names=_parse_factor_names(args.factors),
                adjust_type=args.adjust_type,
                code_version=args.code_version,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

    print(f"factor.input_start_date={result.input_start_date.isoformat()}")
    print(f"factor.input_rows={result.input_rows}")
    print(f"factor.total_rows={result.total_rows}")
    for factor_name, rows_written in result.rows_by_factor.items():
        print(f"factor.{factor_name}.rows={rows_written}")
        print(f"factor.{factor_name}.run_id={result.run_ids[factor_name]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
