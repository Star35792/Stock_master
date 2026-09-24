from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from json import dumps
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.data_sources import HithinkFinanceDataSource
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.repositories.market import SecurityRepository, TradingCalendarRepository
from app.repositories.raw import RawDailyBarImportRepository


@dataclass(frozen=True)
class ReferenceDataSyncResult:
    securities_count: int
    trading_days_count: int


class ReferenceDataSyncService:
    def __init__(
        self,
        *,
        data_source: HithinkFinanceDataSource | None = None,
        security_repository: SecurityRepository | None = None,
        trading_calendar_repository: TradingCalendarRepository | None = None,
        raw_repository: RawDailyBarImportRepository | None = None,
    ) -> None:
        self.data_source = data_source or HithinkFinanceDataSource()
        self.security_repository = security_repository or SecurityRepository()
        self.trading_calendar_repository = (
            trading_calendar_repository or TradingCalendarRepository()
        )
        self.raw_repository = raw_repository or RawDailyBarImportRepository()

    def sync_hithink_reference_data(self, db: Session) -> ReferenceDataSyncResult:
        stocks = self.data_source.fetch_securities(asset_type="a-share")
        by_symbol = {row.symbol: row for row in stocks}
        raw_stock_rounds = [(0, datetime.now(UTC).isoformat(), stocks)]
        conflict_error: str | None = None
        for round_number in range(1, 6):
            if by_symbol and (
                sum(row.list_date is not None for row in by_symbol.values())
                >= len(by_symbol) * 0.95
            ):
                break
            # HiThink's ticker endpoint intermittently omits list_date, even
            # for the same symbol in consecutive responses. Merge only dated
            # observations; never synthesize a listing date from a price bar.
            detailed_rows = self.data_source.fetch_securities(asset_type="a-share", limit=500)
            raw_stock_rounds.append(
                (round_number, datetime.now(UTC).isoformat(), detailed_rows)
            )
            detailed = {row.symbol: row for row in detailed_rows}
            if set(detailed) != set(by_symbol):
                conflict_error = "HiThink security metadata pagination changed the stock universe"
                break
            for symbol, candidate in detailed.items():
                current = by_symbol[symbol]
                if (current.list_date is not None and candidate.list_date is not None
                        and current.list_date != candidate.list_date):
                    conflict_error = f"conflicting HiThink listing dates: {symbol}"
                    break
                if current.list_date is None and candidate.list_date is not None:
                    by_symbol[symbol] = replace(
                        current, list_date=candidate.list_date,
                        raw_payload=candidate.raw_payload,
                    )
            if conflict_error:
                break
        stocks = [by_symbol[row.symbol] for row in stocks]
        if not stocks:
            raise ValueError("HiThink stock reference response is empty")
        # The vendor can omit a listing date in every retry. Keep the known
        # dates already in market.security; an unknown date remains unknown
        # and is excluded per security by the research screen.
        dated_stocks = sum(row.list_date is not None for row in stocks)
        securities = [
            *stocks,
            *self.data_source.fetch_securities(
                asset_type="a-share-index",
                exchanges=("SH", "SZ"),
            ),
        ]
        trading_days = self.data_source.fetch_trading_days()
        if not securities or not trading_days:
            raise ValueError("HiThink reference response is empty")
        captured_at = datetime.now(UTC).isoformat()
        batch = self.raw_repository.create_batch(
            db,
            batch_no=f"hithink_reference_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}",
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset="security_and_trading_calendar",
            request_params={
                "captured_at_utc": captured_at, "raw_first": True,
                "dated_stock_rows": dated_stocks, "stock_rows": len(stocks),
            },
        )
        db.commit()
        try:
            security_payloads = [
                {
                    "observation_round": round_number,
                    "observed_at_utc": observed_at,
                    "vendor_item": record.raw_payload or {
                        "thscode": record.symbol, "ticker": record.raw_symbol,
                        "name": record.name, "exchange": record.exchange,
                        "asset_type": record.security_type, "currency": record.currency,
                        "list_date": (
                            record.list_date.isoformat() if record.list_date else None
                        ),
                    },
                }
                for round_number, observed_at, records in [
                    *raw_stock_rounds,
                    (None, captured_at, securities[len(stocks):]),
                ]
                for record in records
            ]
            calendar_payloads = [
                {
                    "exchange": record.exchange,
                    "trade_date": record.trade_date.isoformat(),
                    "is_open": record.is_open,
                    "vendor_item": record.raw_payload,
                }
                for record in trading_days
            ]
            hashes = []
            for dataset, entries in (
                ("hithink_security_reference", security_payloads),
                ("hithink_calendar_reference", calendar_payloads),
            ):
                for offset in range(0, len(entries), 500):
                    payload = {
                        "captured_at_utc": captured_at,
                        "offset": offset,
                        "items": entries[offset : offset + 500],
                    }
                    row_hash = sha256(
                        dumps(payload, sort_keys=True, ensure_ascii=False).encode()
                    ).hexdigest()
                    db.add(RawVendorSnapshot(
                        batch_id=batch.id,
                        source_name=self.data_source.name,
                        dataset=dataset,
                        snapshot_date=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
                        raw_payload=payload,
                        row_hash=row_hash,
                    ))
                    db.commit()
                    hashes.append(row_hash)
            if conflict_error:
                raise ValueError(conflict_error)
            securities_count = self.security_repository.upsert_many(
                db, records=securities, source_name=self.data_source.name
            )
            trading_days_count = self.trading_calendar_repository.upsert_many(
                db, records=trading_days, source_name=self.data_source.name
            )
            batch.request_params = {**batch.request_params, "raw_page_hashes": hashes}
            self.raw_repository.finish_batch(
                db, batch, status="success",
                row_count=len(securities) + len(trading_days),
                accepted_count=len(securities) + len(trading_days), rejected_count=0,
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            running = db.get(ImportBatch, batch.id)
            if running is not None and running.status == "running":
                self.raw_repository.finish_batch(
                    db, running, status="failed",
                    row_count=len(securities) + len(trading_days),
                    accepted_count=0, rejected_count=len(securities) + len(trading_days),
                    error_message=f"{type(exc).__name__}: {exc}",
                )
                db.commit()
            raise
        return ReferenceDataSyncResult(
            securities_count=securities_count,
            trading_days_count=trading_days_count,
        )
