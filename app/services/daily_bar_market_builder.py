from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.raw import RawDailyBar
from app.repositories.market import (
    DailyBarRepository,
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


class DailyBarMarketBuildService:
    def __init__(
        self,
        *,
        raw_repository: RawDailyBarRepository | None = None,
        market_repository: DailyBarRepository | None = None,
        security_repository: SecurityRepository | None = None,
        trading_calendar_repository: TradingCalendarRepository | None = None,
        issue_repository: DataQualityIssueRepository | None = None,
    ) -> None:
        self.raw_repository = raw_repository or RawDailyBarRepository()
        self.market_repository = market_repository or DailyBarRepository()
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
        for candidate in candidates:
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
            )
            trusted_count += 1

        db.commit()
        return MarketBuildResult(
            candidates_count=len(candidates),
            trusted_count=trusted_count,
            rejected_count=rejected_count,
            issue_count=issue_count,
        )


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


def _exchange_from_symbol(symbol: str) -> str:
    if "." not in symbol:
        return "UNKNOWN"
    return symbol.rsplit(".", 1)[1]
