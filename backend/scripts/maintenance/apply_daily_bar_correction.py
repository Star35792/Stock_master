from __future__ import annotations

import argparse
from datetime import date

from app.database.session import SessionLocal
from app.services import ManualCorrectionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply an audited manual daily-bar correction.")
    parser.add_argument("--symbol", required=True, help="Symbol, for example 600519.SH.")
    parser.add_argument("--trade-date", required=True, help="Trade date, YYYY-MM-DD.")
    parser.add_argument("--field-name", required=True, help="Daily-bar field to correct.")
    parser.add_argument("--new-value", required=True, help="Corrected value.")
    parser.add_argument("--reason", required=True, help="Correction reason.")
    parser.add_argument("--corrected-by", required=True, help="Operator name.")
    parser.add_argument("--adjust-type", default="none", choices=("none", "qfq", "hfq"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SessionLocal() as db:
        result = ManualCorrectionService().apply_daily_bar_correction(
            db,
            symbol=args.symbol,
            trade_date=date.fromisoformat(args.trade_date),
            adjust_type=args.adjust_type,
            field_name=args.field_name,
            new_value=args.new_value,
            reason=args.reason,
            corrected_by=args.corrected_by,
        )

    print(f"correction.symbol={result.symbol}")
    print(f"correction.trade_date={result.trade_date}")
    print(f"correction.adjust_type={result.adjust_type}")
    print(f"correction.field_name={result.field_name}")
    print(f"correction.old_value={result.old_value}")
    print(f"correction.new_value={result.new_value}")
    print(f"correction.quality_status={result.quality_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
