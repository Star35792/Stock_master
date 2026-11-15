from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from json import dumps
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.data_sources import DailyBarRecord, HithinkFinanceDataSource
from app.models.raw import RawDailyBar
from app.repositories.market import SecurityRepository
from app.repositories.raw import RawDailyBarImportRepository
from app.utils.trading_session import is_pre_close_capture

SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DailyBarCollectionResult:
    batch_no: str
    source_name: str
    status: str
    row_count: int
    accepted_count: int
    rejected_count: int
    error_message: str | None
    skipped_intraday: int = 0
    skipped_unchanged: int = 0


class HithinkDailyBarCollectionService:
    def __init__(
        self,
        *,
        data_source: HithinkFinanceDataSource | None = None,
        repository: RawDailyBarImportRepository | None = None,
        security_repository: SecurityRepository | None = None,
    ) -> None:
        self.data_source = data_source or HithinkFinanceDataSource()
        self.repository = repository or RawDailyBarImportRepository()
        self.security_repository = security_repository or SecurityRepository()

    def collect_daily_bars(
        self,
        db: Session,
        *,
        symbols: list[str],
        start_date: date,
        end_date: date,
        adjust_type: str = "none",
        source_priority: int = 50,
        clock: Callable[[], datetime] | None = None,
    ) -> DailyBarCollectionResult:
        """Fetch daily bars into ``raw.daily_bar``.

        A record is skipped when it was captured inside its own session: the
        vendor then answers with the session as it stands, so the "close" is
        the price at that moment and the volume covers only the elapsed part
        of the day.  Such a row is not a daily bar at all, and letting it
        into ``raw`` gives it a chance to be promoted -- or to be compared
        against the settled bars and to accuse them of a conflict.

        The batch keeps the count of what it skipped, so the half-day
        observation still leaves a trace.
        """
        normalized_symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        security_types = self.security_repository.get_security_types(db, set(normalized_symbols))
        captured_at = (clock or (lambda: datetime.now(SHANGHAI)))()
        batch_no = f"hithink_daily_bar_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        batch = self.repository.create_batch(
            db,
            batch_no=batch_no,
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset="daily_bar",
            request_params={
                "symbols": normalized_symbols,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "adjust_type": adjust_type,
                "captured_at": captured_at.isoformat(),
            },
        )

        accepted_count = 0
        skipped_intraday = 0
        skipped_unchanged = 0
        errors: list[str] = []
        pending: list[RawDailyBar] = []

        def flush_pending() -> None:
            nonlocal accepted_count, skipped_unchanged
            if not pending:
                return
            inserted = self.repository.add_daily_bars_if_changed(
                db, pending, content_index=content_index
            )
            accepted_count += inserted
            skipped_unchanged += len(pending) - inserted
            pending.clear()
            db.flush()

        try:
            content_index = self.repository.load_daily_bar_content_index(
                db,
                source_name=self.data_source.name,
                start_date=start_date,
                end_date=end_date,
                adjust_type=adjust_type,
            )
            for symbol in normalized_symbols:
                try:
                    records = self.data_source.fetch_daily_bars(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date,
                        adjust_type=adjust_type,  # type: ignore[arg-type]
                    )
                except Exception as exc:
                    errors.append(f"{symbol}: {exc}")
                    continue

                for record in records:
                    if is_pre_close_capture(captured_at, record.trade_date):
                        skipped_intraday += 1
                        continue
                    pending.append(
                        _to_raw_daily_bar(
                            batch_id=batch.id,
                            source_name=self.data_source.name,
                            source_type=self.data_source.source_type,
                            source_priority=source_priority,
                            record=record,
                            security_type=security_types.get(record.symbol),
                        ),
                    )
                if len(pending) >= 1_000:
                    flush_pending()

            flush_pending()

            rejected_count = len(errors)
            if errors and (accepted_count or skipped_unchanged):
                status = "partial"
            elif errors:
                status = "failed"
            else:
                status = "success"

            notes = list(errors)
            if skipped_intraday:
                notes.append(
                    f"{skipped_intraday} records skipped: captured inside their own "
                    "session, so they are half-day snapshots rather than daily bars"
                )
            error_message = "; ".join(notes) if notes else None
            batch.request_params = {
                **batch.request_params,
                "skipped_unchanged_count": skipped_unchanged,
                "skipped_intraday_count": skipped_intraday,
            }
            self.repository.finish_batch(
                db,
                batch,
                status=status,
                row_count=accepted_count + skipped_unchanged + skipped_intraday + rejected_count,
                accepted_count=accepted_count,
                rejected_count=rejected_count,
                error_message=error_message,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise

        return DailyBarCollectionResult(
            batch_no=batch_no,
            source_name=self.data_source.name,
            status=status,
            row_count=accepted_count + skipped_unchanged + rejected_count + skipped_intraday,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            error_message=error_message,
            skipped_intraday=skipped_intraday,
            skipped_unchanged=skipped_unchanged,
        )


def _to_raw_daily_bar(
    *,
    batch_id: int,
    source_name: str,
    source_type: str,
    source_priority: int,
    record: DailyBarRecord,
    security_type: str | None = None,
) -> RawDailyBar:
    return RawDailyBar(
        batch_id=batch_id,
        source_name=source_name,
        source_type=source_type,
        source_priority=source_priority,
        symbol=record.symbol,
        raw_symbol=record.symbol,
        security_type=security_type or _infer_security_type(record.symbol),
        trade_date=record.trade_date,
        adjust_type=record.adjust_type,
        open=record.open,
        high=record.high,
        low=record.low,
        close=record.close,
        volume=record.volume,
        amount=record.amount,
        raw_payload=record.raw_payload,
        row_hash=_daily_bar_row_hash(record),
    )


def _daily_bar_row_hash(record: DailyBarRecord) -> str:
    payload = {
        "symbol": record.symbol,
        "trade_date": record.trade_date.isoformat(),
        "adjust_type": record.adjust_type,
        "open": str(record.open),
        "high": str(record.high),
        "low": str(record.low),
        "close": str(record.close),
        "volume": str(record.volume) if record.volume is not None else None,
        "amount": str(record.amount) if record.amount is not None else None,
        "raw_payload": record.raw_payload,
    }
    return sha256(dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _infer_security_type(symbol: str) -> str:
    parts = symbol.split(".", 1)
    code = parts[0]
    exchange = parts[1] if len(parts) == 2 else ""
    if code.startswith(("15", "16", "18", "51", "56", "58")):
        return "etf"
    if code.startswith("399") or (exchange == "SH" and code.startswith("000")):
        return "index"
    if code.startswith(("00", "30", "60", "68", "83", "87", "92")):
        return "stock"
    return "unknown"
