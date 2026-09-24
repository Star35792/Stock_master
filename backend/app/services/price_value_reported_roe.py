"""Validate same-day Eastmoney ROE observations for manual screening only."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.data_sources.eastmoney import EastmoneyDataSource
from app.models.raw import RawVendorSnapshot

DATASET = "price_value_reported_roe_observation"
SHANGHAI = ZoneInfo("Asia/Shanghai")
PAGE_SIZE = 500
MAX_ROWS = 20000


@dataclass(frozen=True)
class ReportedRoe:
    ratio: Decimal
    industry: str | None
    report_date: date
    notice_date: date
    disclosed_at: datetime
    raw_page_hash: str


@dataclass(frozen=True)
class ReportedRoeCollection:
    observations: dict[str, ReportedRoe]
    period_observations: dict[str, dict[str, ReportedRoe]]
    raw_page_hashes: list[str]
    source_rows: int
    report_dates: list[str]


def recent_report_dates(target: date, *, count: int = 2) -> list[date]:
    year = target.year
    periods: list[date] = []
    while len(periods) < count:
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            period = date(year, month, day)
            if period < target:
                periods.append(period)
                if len(periods) == count:
                    break
        year -= 1
    return periods


def _parse_date(value: object) -> date:
    return datetime.fromisoformat(str(value)).date()


def _parse_disclosed_at(value: object) -> datetime:
    reported = datetime.fromisoformat(str(value))
    return reported.replace(tzinfo=SHANGHAI) if reported.tzinfo is None else reported


class PriceValueReportedRoeService:
    def __init__(
        self, *, source: EastmoneyDataSource | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.source = source or EastmoneyDataSource()
        self.clock = clock or (lambda: datetime.now(UTC))

    def _capture_time(self, target: date) -> datetime:
        captured = self.clock()
        if captured.tzinfo is None or captured.astimezone(SHANGHAI).date() != target:
            raise ValueError("reported ROE observation crossed its target date")
        return captured.astimezone(UTC)

    def collect_into_batch(
        self,
        db: Session,
        *,
        batch_id: int,
        target: date,
        observed_at: datetime,
    ) -> ReportedRoeCollection:
        if (
            observed_at.tzinfo is None
            or observed_at.astimezone(SHANGHAI).date() != target
            or self._capture_time(target).astimezone(SHANGHAI).date() != target
        ):
            raise ValueError("reported ROE is only publishable for its actual observation date")
        observations: dict[str, ReportedRoe] = {}
        period_observations: dict[str, dict[str, ReportedRoe]] = {}
        page_hashes: list[str] = []
        source_rows = 0
        report_dates: list[str] = []
        # A quarter that has just ended often has no published reports yet.
        # Stop after two eligible periods even when their security sets match.
        periods = recent_report_dates(target, count=6)
        for period in periods:
            eligible_rows: dict[str, ReportedRoe] = {}
            expected_total: int | None = None
            seen_in_period: set[str] = set()
            received = 0
            eligible_in_period = 0
            for page in range(1, MAX_ROWS // PAGE_SIZE + 2):
                response, raw_text = self.source.fetch_reported_roe_page(
                    report_date=period, page=page, page_size=PAGE_SIZE
                )
                captured = self.clock()
                fetched_at = captured.astimezone(UTC).isoformat()
                page_hash = sha256(raw_text.encode("utf-8")).hexdigest()
                db.add(
                    RawVendorSnapshot(
                        batch_id=batch_id,
                        source_name=self.source.name,
                        dataset=DATASET,
                        snapshot_date=target,
                        raw_payload={
                            "report_date": period.isoformat(),
                            "target_trade_date": target.isoformat(),
                            "page": page,
                            "fetched_at_utc": fetched_at,
                            "response_json": raw_text,
                        },
                        row_hash=page_hash,
                    )
                )
                db.commit()
                page_hashes.append(page_hash)
                if captured.tzinfo is None or captured.astimezone(SHANGHAI).date() != target:
                    raise ValueError("reported ROE observation crossed its target date")
                result = response.get("result")
                if response.get("success") is not True:
                    raise ValueError("supplier rejected reported ROE page")
                if result is None and page == 1:
                    expected_total = 0
                    break
                if not isinstance(result, dict):
                    raise ValueError("supplier rejected reported ROE page")
                total = result.get("count")
                items = result.get("data")
                pages = result.get("pages")
                if page == 1 and total == 0 and items in (None, []) and pages in (None, 0):
                    expected_total = 0
                    break
                if (
                    not isinstance(total, int) or isinstance(total, bool)
                    or not 1 <= total <= MAX_ROWS
                    or not isinstance(items, list)
                    or pages != (total + PAGE_SIZE - 1) // PAGE_SIZE
                    or (expected_total is not None and total != expected_total)
                    or len(items) != min(PAGE_SIZE, total - received)
                ):
                    raise ValueError("reported ROE pagination is incomplete")
                expected_total = total
                for item in items:
                    if not isinstance(item, dict) or _parse_date(item.get("REPORTDATE")) != period:
                        raise ValueError("reported ROE row has wrong report date")
                    symbol = item.get("SECUCODE")
                    if not isinstance(symbol, str) or symbol in seen_in_period:
                        raise ValueError("duplicate or invalid reported ROE security")
                    seen_in_period.add(symbol)
                    if not symbol.endswith((".SH", ".SZ", ".BJ")):
                        continue
                    try:
                        notice = _parse_date(item.get("NOTICE_DATE"))
                        disclosed = _parse_disclosed_at(item.get("EITIME"))
                    except ValueError:
                        continue
                    if notice > target or disclosed > observed_at:
                        continue
                    raw_roe = item.get("WEIGHTAVG_ROE")
                    try:
                        ratio = Decimal(str(raw_roe)) / Decimal("100")
                    except (InvalidOperation, TypeError):
                        continue
                    if not ratio.is_finite():
                        continue
                    eligible_in_period += 1
                    board_name = item.get("BOARD_NAME")
                    observation = ReportedRoe(
                        ratio=ratio,
                        industry=str(board_name).strip() if board_name else None,
                        report_date=period,
                        notice_date=notice,
                        disclosed_at=disclosed,
                        raw_page_hash=page_hash,
                    )
                    eligible_rows[symbol] = observation
                    observations.setdefault(symbol, observation)
                received += len(items)
                source_rows += len(items)
                if page == pages:
                    break
            if expected_total is None or received != expected_total:
                raise ValueError("reported ROE response is incomplete")
            if eligible_in_period:
                report_dates.append(period.isoformat())
                period_observations[period.isoformat()] = eligible_rows
                if len(report_dates) == 2:
                    break
        self._capture_time(target)
        return ReportedRoeCollection(
            observations=observations,
            period_observations=period_observations,
            raw_page_hashes=page_hashes,
            source_rows=source_rows,
            report_dates=report_dates,
        )
