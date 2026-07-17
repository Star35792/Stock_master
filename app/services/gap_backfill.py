from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from app.data_sources.fallback import FallbackDailyBarDataSource
from app.data_sources.hithink import HithinkFinanceDataSource
from app.repositories.market import DailyBarRepository, SecurityRepository
from app.repositories.raw import RawDailyBarImportRepository
from app.services.hithink_collection import _to_raw_daily_bar


@dataclass(frozen=True)
class GapBackfillResult:
    batch_no: str | None
    expected_rows: int
    fetched_rows: int
    inserted_rows: int
    skipped_rows: int


class MissingDailyBarBackfillService:
    """Fill only calendar gaps left by the primary HiThink dataset."""

    def __init__(
        self,
        *,
        primary_data_source: HithinkFinanceDataSource | None = None,
        data_source: FallbackDailyBarDataSource | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
        security_repository: SecurityRepository | None = None,
        repository: RawDailyBarImportRepository | None = None,
    ) -> None:
        self.primary_data_source = primary_data_source or HithinkFinanceDataSource()
        self.data_source = data_source or FallbackDailyBarDataSource()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.repository = repository or RawDailyBarImportRepository()

    def backfill(
        self,
        db: Session,
        *,
        symbols: list[str],
        start_date: date,
        end_date: date,
        adjust_type: str = "qfq",
        source_priority: int = 80,
    ) -> GapBackfillResult:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if adjust_type != "qfq":
            raise ValueError("fallback K-line backfill currently supports qfq only")
        normalized = sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
        expected = []
        for symbol in normalized:
            rows, _ = self.daily_bar_repository.list_missing_by_calendar(
                db,
                start_date=start_date,
                end_date=end_date,
                adjust_type=adjust_type,
                symbol=symbol,
            )
            expected.extend(rows)
        expected_keys = {(row.symbol, row.trade_date): row for row in expected}
        if not expected_keys:
            return GapBackfillResult(None, 0, 0, 0, 0)

        batch_no = f"gap_backfill_{datetime.now(UTC):%Y%m%d%H%M%S}_{uuid4().hex[:8]}"
        batch = self.repository.create_batch(
            db,
            batch_no=batch_no,
            source_name=self.data_source.name,
            source_type=self.data_source.source_type,
            dataset="daily_bar_gap_backfill",
            request_params={
                "symbols": normalized,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "adjust_type": adjust_type,
            },
        )
        fetched = inserted = skipped = 0
        source_errors: list[str] = []
        try:
            for symbol in normalized:
                symbol_keys = {key for key in expected_keys if key[0] == symbol}
                if not symbol_keys:
                    continue
                primary_records = []
                try:
                    primary_records = self.primary_data_source.fetch_daily_bars(
                        symbol=symbol,
                        start_date=start_date,
                        end_date=end_date,
                        adjust_type=adjust_type,  # type: ignore[arg-type]
                    )
                except Exception as exc:
                    source_errors.append(f"{symbol} primary: {exc}")
                primary_by_key = {
                    (record.symbol, record.trade_date): record for record in primary_records
                }
                remaining = symbol_keys - primary_by_key.keys()
                fallback_records = []
                if remaining:
                    try:
                        fallback_records = self.data_source.fetch_daily_bars(
                            symbol=symbol,
                            start_date=start_date,
                            end_date=end_date,
                            adjust_type=adjust_type,
                        )
                    except Exception as exc:
                        source_errors.append(f"{symbol} fallback: {exc}")
                records = [*primary_records, *fallback_records]
                fallback_source_name = (
                    self.data_source.last_source_name or self.data_source.name
                )
                fetched += len(records)
                seen_keys = set()
                for record in records:
                    key = (record.symbol, record.trade_date)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    expected_row = expected_keys.get(key)
                    if expected_row is None:
                        skipped += 1
                        continue
                    db.add(
                        _to_raw_daily_bar(
                            batch_id=batch.id,
                            source_name=(
                                self.primary_data_source.name
                                if key in primary_by_key
                                else fallback_source_name
                            ),
                            source_type=(
                                self.primary_data_source.source_type
                                if key in primary_by_key
                                else self.data_source.source_type
                            ),
                            source_priority=(
                                max(source_priority - 30, 0)
                                if key in primary_by_key
                                else source_priority
                            ),
                            record=record,
                            security_type=expected_row.security_type,
                        )
                    )
                    inserted += 1
            self.repository.finish_batch(
                db,
                batch,
                status="success" if inserted else "failed",
                row_count=fetched,
                accepted_count=inserted,
                rejected_count=max(fetched - inserted, 0),
                error_message="; ".join(source_errors) if source_errors and not inserted else None,
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        if inserted == 0:
            details = "; ".join(source_errors) or "all sources returned no rows"
            raise RuntimeError(f"unable to backfill missing K-lines for {normalized}: {details}")
        return GapBackfillResult(batch_no, len(expected_keys), fetched, inserted, skipped)
