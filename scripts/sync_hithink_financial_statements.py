from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.database.session import SessionLocal
from app.models.market import Security
from app.services.hithink_financial_collection import HithinkFinancialCollectionService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch-sync HiThink financial statements only.")
    parser.add_argument("--period", choices=("annual", "quarterly"), default="quarterly")
    parser.add_argument("--statement-limit", type=int, default=5)
    parser.add_argument("--start-after", default="")
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--sleep-seconds", type=float, default=0.05)
    parser.add_argument(
        "--failure-log",
        default="data/financial_statement_sync_failures.log",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    with SessionLocal() as db:
        symbols = list(
            db.scalars(
                select(Security.symbol)
                .where(
                    Security.security_type == "stock",
                    Security.symbol > args.start_after,
                )
                .order_by(Security.symbol)
            )
        )
    if args.max_symbols:
        symbols = symbols[: args.max_symbols]
    if not symbols:
        print("No symbols to sync.")
        return 0

    failure_path = Path(args.failure_log)
    service = HithinkFinancialCollectionService()
    success = failures = 0
    for index, symbol in enumerate(symbols, 1):
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with SessionLocal() as db:
                    result = service.collect_symbol(
                        db,
                        symbol=symbol,
                        period=args.period,
                        limit=args.statement_limit,
                        indicator_reports=[],
                    )
                success += 1
                print(
                    f"[{index}/{len(symbols)}] {symbol} statements={result.statement_rows}",
                    flush=True,
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        else:
            failures += 1
            failure_path.parent.mkdir(parents=True, exist_ok=True)
            with failure_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"{datetime.now(UTC).isoformat()}\t{symbol}\t"
                    f"{type(last_error).__name__}: {last_error}\n"
                )
            print(f"[{index}/{len(symbols)}] {symbol} FAILED: {last_error}", flush=True)
        if args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)
    print(f"completed={success} failed={failures} total={len(symbols)}")
    print(f"failure_log={failure_path}")
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
