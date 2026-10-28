"""Resumable financial-statement preparation; network parallel, database serial."""

from __future__ import annotations

import argparse
import calendar
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.data_sources import HithinkFinanceDataSource
from app.models.fundamental import FinancialStatement
from app.models.market import Security
from app.services.hithink_financial_collection import HithinkFinancialCollectionService
from app.utils.duckdb_write_lock import duckdb_write_lock

TYPES = ("income", "balance", "cash_flow")
LIMITS = {"annual": 4, "quarterly": 7}
BACKEND = Path(__file__).resolve().parents[2]


def required_periods(as_of: date, period: str) -> set[date]:
    if period == "annual":
        return {date(y, 12, 31) for y in range(as_of.year - 3, as_of.year)}
    # Latest seven completed calendar quarters, not seven arbitrary old rows.
    current = as_of.year * 4 + (as_of.month - 1) // 3
    result = set()
    for number in range(current - 7, current):
        year, quarter = divmod(number, 4)
        month = (quarter + 1) * 3
        result.add(date(year, month, calendar.monthrange(year, month)[1]))
    return result


def coverage(db, symbol: str, period: str, as_of: date) -> dict:
    rows = db.execute(
        select(FinancialStatement.statement_type, FinancialStatement.period_end).where(
            FinancialStatement.symbol == symbol,
            FinancialStatement.period == period,
            FinancialStatement.report_date <= as_of,
            FinancialStatement.currency == "CNY",
        )
    ).all()
    required = required_periods(as_of, period)
    missing = {
        kind: sorted(
            d.isoformat() for d in required - {end for row_kind, end in rows if row_kind == kind}
        )
        for kind in TYPES
    }
    return {"complete": not any(missing.values()), "missing_periods": missing}


def fetch_symbol(symbol: str, periods: list[str], source_factory=HithinkFinanceDataSource):
    source = source_factory()
    fetched = {}
    for period in periods:
        try:
            cached = {
                kind: source.fetch_financial_statements(
                    symbol=symbol, statement_type=kind, period=period, limit=LIMITS[period]
                )
                for kind in TYPES
            }
            if any(not items for items in cached.values()):
                raise ValueError("empty financial statement response")
            if any(
                str(item.get("thscode", "")).strip().upper() != symbol
                or item.get("period") != period
                for items in cached.values()
                for item in items
            ):
                raise ValueError("response symbol or period mismatch")
            fetched[period] = {"cache": cached, "status": "fetched"}
        except Exception as exc:
            # Vendor exception strings can contain request credentials. Record
            # only the exception class and a safe, fixed diagnostic.
            fetched[period] = {
                "status": "fetch_failed",
                "error": type(exc).__name__,
                "detail": "statement fetch failed; retry this phase",
            }
    return symbol, fetched


class ReplaySource:
    name = HithinkFinanceDataSource.name
    source_type = HithinkFinanceDataSource.source_type

    def __init__(self, symbol: str, period: str, cache: dict):
        self.symbol, self.period, self.cache = symbol, period, cache

    def fetch_financial_statements(self, *, symbol, statement_type, period, limit):
        if (symbol, period, limit) != (self.symbol, self.period, LIMITS[self.period]):
            raise ValueError("replay request mismatch")
        return self.cache[statement_type]


def replay_phase(
    db,
    symbol: str,
    period: str,
    fetched: dict,
    as_of: date,
    service_factory=HithinkFinancialCollectionService,
) -> dict:
    if fetched["status"] != "fetched":
        return fetched
    try:
        result = service_factory(
            data_source=ReplaySource(symbol, period, fetched["cache"])
        ).collect_symbol(
            db, symbol=symbol, period=period, limit=LIMITS[period], indicator_reports=[]
        )
        state = coverage(db, symbol, period, as_of)
        return {
            "status": "success" if state["complete"] else "incomplete",
            "batch_no": result.batch_no,
            "raw_rows": result.raw_rows,
            "statement_rows": result.statement_rows,
            **state,
        }
    except Exception as exc:
        db.rollback()
        return {
            "status": "write_failed",
            "error": type(exc).__name__,
            "detail": "raw/financial collection failed; retry this phase",
        }


def save_report(output: Path, report: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    report["updated_at"] = datetime.now(UTC).isoformat()
    report["phase_counts"] = {
        phase: {
            state: sum(row.get(phase, {}).get("status") == state for row in report["symbols"])
            for state in (
                "success",
                "skipped_complete",
                "incomplete",
                "fetch_failed",
                "write_failed",
            )
        }
        for phase in LIMITS
    }
    temp = output.with_name(output.name + f".{uuid4().hex[:8]}.tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--symbols", help="Comma-separated explicit symbols")
    scope.add_argument("--all-market", action="store_true")
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-complete", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or (args.max_symbols is not None and args.max_symbols < 1):
        parser.error("workers must be 1..8; max-symbols must be positive")
    explicit = sorted({s.strip().upper() for s in (args.symbols or "").split(",") if s.strip()})
    if args.symbols is not None and not explicit:
        parser.error("--symbols must not be empty")
    # Session initialization can write schema compatibility; import it only
    # after acquiring the same lock as every subsequent database mutation.
    from app.config.settings import settings

    as_of = datetime.now(UTC).date()
    report = {
        "started_at": datetime.now(UTC).isoformat(),
        "as_of": as_of.isoformat(),
        "status": "running",
        "scope": "all_market" if args.all_market else explicit,
        "workers": args.workers,
        "symbols": [],
    }
    try:
        with duckdb_write_lock(
            Path(settings.metadata_db_path).resolve().parent / "duckdb_write.lock"
        ):
            from app.database.session import SessionLocal

            with SessionLocal() as db, ThreadPoolExecutor(max_workers=args.workers) as pool:
                symbols = (
                    list(
                        db.scalars(
                            select(Security.symbol)
                            .where(Security.security_type == "stock", Security.is_active.is_(True))
                            .order_by(Security.symbol)
                        )
                    )
                    if args.all_market
                    else explicit
                )
                if args.max_symbols:
                    symbols = symbols[: args.max_symbols]
                report["requested_symbols"] = len(symbols)
                save_report(args.output, report)
                # Bound network cache memory to one worker-sized batch.
                for offset in range(0, len(symbols), args.workers):
                    jobs, states = [], {}
                    for symbol in symbols[offset : offset + args.workers]:
                        state = {"symbol": symbol}
                        needed = []
                        for phase in LIMITS:
                            current = coverage(db, symbol, phase, as_of)
                            if args.skip_complete and current["complete"]:
                                state[phase] = {"status": "skipped_complete", **current}
                            else:
                                needed.append(phase)
                        states[symbol] = state
                        jobs.append(pool.submit(fetch_symbol, symbol, needed))
                    for job in jobs:
                        symbol, fetched = job.result()
                        state = states[symbol]
                        for phase, payload in fetched.items():
                            state[phase] = replay_phase(db, symbol, phase, payload, as_of)
                        report["symbols"].append(state)
                        save_report(args.output, report)
                        print(
                            f"{len(report['symbols'])}/{len(symbols)} {symbol} "
                            f"annual={state['annual']['status']} "
                            f"quarterly={state['quarterly']['status']}",
                            flush=True,
                        )
        report["status"] = (
            "success"
            if all(
                row[phase]["status"] in {"success", "skipped_complete"}
                for row in report["symbols"]
                for phase in LIMITS
            )
            else "partial"
        )
    except Exception as exc:
        report.update(
            status="failed",
            error=type(exc).__name__,
            detail="preparation interrupted; rerun with --skip-complete",
        )
    finally:
        save_report(args.output, report)
    return 0 if report["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
