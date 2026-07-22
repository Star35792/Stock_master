from __future__ import annotations

import argparse
from datetime import date

from app.database.session import SessionLocal
from app.scheduler.factor_pipeline import run_all_factors


def _parse_symbols(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [symbol.strip().upper() for symbol in value.split(",") if symbol.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch-compute all factor services for a date range (idempotent)."
    )
    parser.add_argument("--start-date", required=True, help="Inclusive start date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Inclusive end date, YYYY-MM-DD.")
    parser.add_argument("--symbols", help="Optional comma-separated symbols; omit for all.")
    parser.add_argument(
        "--adjust-type",
        default="qfq",
        choices=("none", "qfq", "hfq"),
        help="Daily bar adjustment type.",
    )
    parser.add_argument(
        "--code-version",
        default="local",
        help="Code version recorded in factor runs.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = run_all_factors(
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
        symbols=_parse_symbols(args.symbols),
        adjust_type=args.adjust_type,
        code_version=args.code_version,
        session_factory=SessionLocal,
    )
    print(f"batch.start_date={result.start_date.isoformat()}")
    print(f"batch.end_date={result.end_date.isoformat()}")
    print(f"batch.adjust_type={result.adjust_type}")
    print(f"batch.total_rows={result.total_rows}")
    for name, svc in result.services.items():
        status = svc.get("status")
        if status == "success":
            total = svc["total_rows"]
            inp = svc.get("input_rows", 0)
            print(f"  {name}: success total_rows={total} input_rows={inp}")
        else:
            print(f"  {name}: FAILED {svc.get('error', '')}")
    if result.failed:
        print(f"batch.failed_services={','.join(result.failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
