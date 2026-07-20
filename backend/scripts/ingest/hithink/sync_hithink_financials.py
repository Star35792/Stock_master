from __future__ import annotations

import argparse

from app.data_sources import HithinkFinanceDataSource
from app.database.session import SessionLocal
from app.lake import DataLake
from app.services.hithink_financial_collection import HithinkFinancialCollectionService


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync HiThink financial data.")
    parser.add_argument("--symbols", required=True, help="Comma-separated thscodes.")
    parser.add_argument("--period", choices=("annual", "quarterly"), default="annual")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--indicator-reports",
        default="",
        help="Comma-separated reports such as 2025-3,2025-4.",
    )
    parser.add_argument("--lake-only", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    symbols = [symbol.upper() for symbol in _csv(args.symbols)]
    if not symbols:
        raise ValueError("symbols must not be empty")
    reports = _csv(args.indicator_reports)
    if args.lake_only:
        source = HithinkFinanceDataSource()
        lake = DataLake()
        for symbol in symbols:
            for statement_type in ("income", "balance", "cash_flow"):
                rows = source.fetch_financial_statements(
                    symbol=symbol,
                    statement_type=statement_type,
                    period=args.period,
                    limit=args.limit,
                )
                lake.append_payloads(f"financial_{statement_type}", rows)
            indicators = [
                source.fetch_financial_indicators(symbol=symbol, report=report)
                for report in reports
            ]
            if indicators:
                lake.append_payloads("financial_indicators", indicators)
        return 0
    service = HithinkFinancialCollectionService()
    with SessionLocal() as db:
        for symbol in symbols:
            result = service.collect_symbol(
                db,
                symbol=symbol,
                period=args.period,
                limit=args.limit,
                indicator_reports=reports,
            )
            print(
                f"{symbol}: statements={result.statement_rows} "
                f"indicators={result.indicator_rows} raw={result.raw_rows}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
