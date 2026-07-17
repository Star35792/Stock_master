from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from json import dumps
from uuid import uuid4

from sqlalchemy.orm import Session

from app.data_sources import DailyBarRecord, HithinkFinanceDataSource
from app.models.raw import RawDailyBar
from app.repositories.raw import RawDailyBarImportRepository


@dataclass(frozen=True)
class DailyBarCollectionResult:
    batch_no: str
    source_name: str
    status: str
    row_count: int
    accepted_count: int
    rejected_count: int
    error_message: str | None


class HithinkDailyBarCollectionService:
    def __init__(
        self,
        *,
        data_source: HithinkFinanceDataSource | None = None,
        repository: RawDailyBarImportRepository | None = None,
    ) -> None:
        self.data_source = data_source or HithinkFinanceDataSource()
        self.repository = repository or RawDailyBarImportRepository()

    def collect_daily_bars(
        self,
        db: Session,
        *,
        symbols: list[str],
        start_date: date,
        end_date: date,
        adjust_type: str = "none",
        source_priority: int = 50,
    ) -> DailyBarCollectionResult:
        normalized_symbols = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
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
            },
        )

        accepted_count = 0
        errors: list[str] = []

        try:
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
                    self.repository.add_daily_bar(
                        db,
                        _to_raw_daily_bar(
                            batch_id=batch.id,
                            source_name=self.data_source.name,
                            source_type=self.data_source.source_type,
                            source_priority=source_priority,
                            record=record,
                        ),
                    )
                    accepted_count += 1

            rejected_count = len(errors)
            if errors and accepted_count:
                status = "partial"
            elif errors:
                status = "failed"
            else:
                status = "success"

            error_message = "; ".join(errors) if errors else None
            self.repository.finish_batch(
                db,
                batch,
                status=status,
                row_count=accepted_count + rejected_count,
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
            row_count=accepted_count + rejected_count,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            error_message=error_message,
        )


def _to_raw_daily_bar(
    *,
    batch_id: int,
    source_name: str,
    source_type: str,
    source_priority: int,
    record: DailyBarRecord,
) -> RawDailyBar:
    return RawDailyBar(
        batch_id=batch_id,
        source_name=source_name,
        source_type=source_type,
        source_priority=source_priority,
        symbol=record.symbol,
        raw_symbol=record.symbol,
        security_type=_infer_security_type(record.symbol),
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
    code = symbol.split(".", 1)[0]
    if code.startswith(("15", "16", "18", "51", "56", "58")):
        return "etf"
    if code.startswith(("00", "30", "60", "68", "83", "87", "92")):
        return "stock"
    return "unknown"
