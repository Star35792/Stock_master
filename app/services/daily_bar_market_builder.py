from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from itertools import combinations

from sqlalchemy.orm import Session

from app.models.raw import RawDailyBar
from app.repositories.market import (
    DailyBarRepository,
    DailyBarSourceCompareRepository,
    SecurityRepository,
    TradingCalendarRepository,
)
from app.repositories.raw import RawDailyBarRepository
from app.repositories.system import DataQualityIssueRepository


@dataclass(frozen=True)
class MarketBuildResult:
    candidates_count: int
    trusted_count: int
    rejected_count: int
    issue_count: int
    conflict_count: int = 0


class DailyBarMarketBuildService:
    def __init__(
        self,
        *,
        raw_repository: RawDailyBarRepository | None = None,
        market_repository: DailyBarRepository | None = None,
        compare_repository: DailyBarSourceCompareRepository | None = None,
        security_repository: SecurityRepository | None = None,
        trading_calendar_repository: TradingCalendarRepository | None = None,
        issue_repository: DataQualityIssueRepository | None = None,
    ) -> None:
        self.raw_repository = raw_repository or RawDailyBarRepository()
        self.market_repository = market_repository or DailyBarRepository()
        self.compare_repository = compare_repository or DailyBarSourceCompareRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.trading_calendar_repository = (
            trading_calendar_repository or TradingCalendarRepository()
        )
        self.issue_repository = issue_repository or DataQualityIssueRepository()

    def build_from_raw(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        adjust_type: str = "none",
        source_name: str | None = None,
    ) -> MarketBuildResult:
        candidates = self.raw_repository.latest_candidates(
            db,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            source_name=source_name,
        )
        raw_groups = self.raw_repository.latest_candidates_by_source(
            db,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            source_name=source_name,
        )
        reconciliation = self._reconcile_sources(db, raw_groups)
        existing_symbols = self.security_repository.get_symbols(
            db, {candidate.symbol for candidate in candidates}
        )

        open_dates_by_exchange = {
            exchange: self.trading_calendar_repository.get_open_dates(
                db,
                exchange=exchange,
                dates={
                    candidate.trade_date
                    for candidate in candidates
                    if _exchange_from_symbol(candidate.symbol) == exchange
                },
            )
            for exchange in {"SH", "SZ", "BJ"}
        }

        trusted_count = 0
        rejected_count = 0
        issue_count = 0
        conflict_count = 0
        for candidate in candidates:
            key = (candidate.symbol, candidate.trade_date, candidate.adjust_type)
            if key in reconciliation.conflict_issues:
                rejected_count += 1
                conflict_count += 1
                for issue in reconciliation.conflict_issues[key]:
                    self.issue_repository.create(db, **issue)
                    issue_count += 1
                continue

            issues = _validate_candidate(
                candidate,
                existing_symbols=existing_symbols,
                open_dates_by_exchange=open_dates_by_exchange,
            )
            if issues:
                rejected_count += 1
                for issue in issues:
                    self.issue_repository.create(db, **issue)
                    issue_count += 1
                continue

            self.market_repository.upsert_from_raw(
                db,
                raw_bar=candidate,
                quality_score=Decimal("100.00"),
                quality_status="trusted",
                is_suspect=False,
                source_count=reconciliation.source_counts.get(key, 1),
            )
            trusted_count += 1

        db.commit()
        return MarketBuildResult(
            candidates_count=len(candidates),
            trusted_count=trusted_count,
            rejected_count=rejected_count,
            issue_count=issue_count,
            conflict_count=conflict_count,
        )

    def _reconcile_sources(
        self,
        db: Session,
        raw_groups: dict[tuple[str, date, str], list[RawDailyBar]],
    ) -> "_ReconciliationResult":
        conflict_issues: dict[tuple[str, date, str], list[dict]] = {}
        source_counts: dict[tuple[str, date, str], int] = {}

        for key, rows in raw_groups.items():
            if len(rows) < 2:
                source_counts[key] = len(rows)
                continue

            sorted_rows = sorted(rows, key=lambda row: (row.source_name, row.id))
            source_counts[key] = len(sorted_rows)
            for left, right in combinations(sorted_rows, 2):
                comparison = _compare_candidates(left, right)
                self.compare_repository.upsert_pair(db, **comparison)
                if comparison["is_conflict"]:
                    conflict_issues.setdefault(key, []).append(
                        _source_conflict_issue(left, right, comparison)
                    )

        return _ReconciliationResult(
            conflict_issues=conflict_issues,
            source_counts=source_counts,
        )


@dataclass(frozen=True)
class _ReconciliationResult:
    conflict_issues: dict[tuple[str, date, str], list[dict]]
    source_counts: dict[tuple[str, date, str], int]


def _validate_candidate(
    candidate: RawDailyBar,
    *,
    existing_symbols: set[str],
    open_dates_by_exchange: dict[str, set[date]],
) -> list[dict]:
    issues: list[dict] = []
    base = {
        "dataset": "daily_bar",
        "symbol": candidate.symbol,
        "trade_date": candidate.trade_date,
        "adjust_type": candidate.adjust_type,
        "source_name": candidate.source_name,
        "raw_id": candidate.id,
    }

    if candidate.symbol not in existing_symbols:
        issues.append(
            {
                **base,
                "issue_type": "SECURITY_NOT_FOUND",
                "severity": "error",
                "description": "security master data is missing",
            }
        )

    exchange = _exchange_from_symbol(candidate.symbol)
    if candidate.trade_date not in open_dates_by_exchange.get(exchange, set()):
        issues.append(
            {
                **base,
                "issue_type": "CALENDAR_MISMATCH",
                "severity": "error",
                "description": "trade_date is not an open trading day for the exchange",
                "expected_value": {"exchange": exchange, "is_open": True},
                "actual_value": {"trade_date": candidate.trade_date.isoformat()},
            }
        )

    prices = {
        "open": candidate.open,
        "high": candidate.high,
        "low": candidate.low,
        "close": candidate.close,
    }
    if any(value is None or value <= 0 for value in prices.values()):
        issues.append(
            {
                **base,
                "issue_type": "PRICE_INVALID",
                "severity": "error",
                "description": "OHLC fields must be present and positive",
                "actual_value": {key: str(value) for key, value in prices.items()},
            }
        )
    elif not (
        candidate.high >= candidate.open
        and candidate.high >= candidate.close
        and candidate.high >= candidate.low
        and candidate.low <= candidate.open
        and candidate.low <= candidate.close
        and candidate.low <= candidate.high
    ):
        issues.append(
            {
                **base,
                "issue_type": "PRICE_INVALID",
                "severity": "error",
                "description": "OHLC high/low relationship is invalid",
                "actual_value": {key: str(value) for key, value in prices.items()},
            }
        )

    if candidate.volume is not None and candidate.volume < 0:
        issues.append(
            {
                **base,
                "issue_type": "VOLUME_INVALID",
                "severity": "error",
                "description": "volume must not be negative",
                "actual_value": {"volume": str(candidate.volume)},
            }
        )
    if candidate.amount is not None and candidate.amount < 0:
        issues.append(
            {
                **base,
                "issue_type": "AMOUNT_INVALID",
                "severity": "error",
                "description": "amount must not be negative",
                "actual_value": {"amount": str(candidate.amount)},
            }
        )

    return issues


def _compare_candidates(left: RawDailyBar, right: RawDailyBar) -> dict:
    if left.id > right.id:
        left, right = right, left

    price_fields = ("open", "high", "low", "close")
    different_price_fields = [
        field
        for field in price_fields
        if getattr(left, field) is not None
        and getattr(right, field) is not None
        and getattr(left, field) != getattr(right, field)
    ]
    close_diff = _diff(left.close, right.close)
    is_conflict = bool(different_price_fields)
    return {
        "symbol": left.symbol,
        "trade_date": left.trade_date,
        "adjust_type": left.adjust_type,
        "source_a": left.source_name,
        "source_b": right.source_name,
        "raw_id_a": left.id,
        "raw_id_b": right.id,
        "close_diff": close_diff,
        "close_diff_pct": _pct_diff(left.close, right.close),
        "volume_diff_pct": _pct_diff(left.volume, right.volume),
        "amount_diff_pct": _pct_diff(left.amount, right.amount),
        "is_conflict": is_conflict,
        "conflict_reason": (
            f"price fields differ: {', '.join(different_price_fields)}"
            if different_price_fields
            else None
        ),
    }


def _source_conflict_issue(left: RawDailyBar, right: RawDailyBar, comparison: dict) -> dict:
    return {
        "dataset": "daily_bar",
        "symbol": left.symbol,
        "trade_date": left.trade_date,
        "adjust_type": left.adjust_type,
        "source_name": f"{left.source_name},{right.source_name}",
        "raw_id": left.id,
        "issue_type": "SOURCE_CONFLICT",
        "severity": "error",
        "description": "daily bar price differs between sources",
        "expected_value": {
            left.source_name: _price_snapshot(left),
            right.source_name: _price_snapshot(right),
        },
        "actual_value": {
            "raw_id_a": left.id,
            "raw_id_b": right.id,
            "close_diff": str(comparison["close_diff"]),
            "conflict_reason": comparison["conflict_reason"],
        },
    }


def _price_snapshot(row: RawDailyBar) -> dict[str, str | None]:
    return {
        "open": _decimal_to_str(row.open),
        "high": _decimal_to_str(row.high),
        "low": _decimal_to_str(row.low),
        "close": _decimal_to_str(row.close),
    }


def _diff(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    return left - right


def _pct_diff(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right in (None, Decimal("0")):
        return None
    return (left - right) / right


def _decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _exchange_from_symbol(symbol: str) -> str:
    if "." not in symbol:
        return "UNKNOWN"
    return symbol.rsplit(".", 1)[1]
