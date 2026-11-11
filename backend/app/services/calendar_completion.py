"""Complete bounded calendar dates from an audited full open-day response."""

import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy.orm import Session

from app.data_sources.base import TradingDayRecord
from app.models.raw import RawVendorSnapshot
from app.repositories.market.calendar_observation import CalendarObservationRepository
from app.repositories.market.trading_calendar import TradingCalendarRepository
from app.repositories.raw import RawDailyBarImportRepository
from app.services.job_logging import logged_job


class CalendarCompletionService:
    def run(self, db: Session, *, start: date, end: date) -> dict:
        repository = CalendarObservationRepository()
        source, pages = repository.latest(db)
        if source.source_name != "hithink_finance":
            raise ValueError("unsupported full-list calendar source")
        pages.sort(key=lambda page: page.raw_payload["offset"])
        expected_offset, entries, hashes = 0, [], []
        declared = set((source.request_params or {}).get("raw_page_hashes", []))
        for page in pages:
            payload = page.raw_payload
            digest = sha256(json.dumps(
                payload, sort_keys=True, ensure_ascii=False,
            ).encode()).hexdigest()
            if (digest != page.row_hash or digest not in declared
                    or payload["offset"] != expected_offset
                    or not 0 < len(payload["items"]) <= 500):
                raise ValueError("calendar raw pagination or hash unverified")
            entries.extend(payload["items"])
            expected_offset += len(payload["items"])
            hashes.append(digest)
        if not pages or len(pages[-1].raw_payload["items"]) == 500:
            raise ValueError("calendar full-list boundary unverified")
        sets = {exchange: set() for exchange in ("SH", "SZ", "BJ")}
        for item in entries:
            if item["exchange"] not in sets or item["is_open"] is not True:
                raise ValueError("unexpected open-list calendar row")
            sets[item["exchange"]].add(date.fromisoformat(item["trade_date"]))
        opened = sets["SH"]
        if not opened or any(days != opened for days in sets.values()):
            raise ValueError("calendar exchange mapping conflict")
        if len(entries) != 3 * len(opened) or not min(opened) <= start <= end <= max(opened):
            raise ValueError("calendar incomplete or requested range outside source bounds")
        existing = {(row.exchange, row.trade_date): row for row in repository.existing(
            db, start=start, end=end,
        )}
        records = []
        day = start
        while day <= end:
            for exchange in sets:
                value = day in opened
                row = existing.get((exchange, day))
                if row is not None and row.is_open != value:
                    raise ValueError("existing calendar conflicts with full-list observation")
                if row is None:
                    records.append(TradingDayRecord(exchange, day, is_open=value))
            day += timedelta(days=1)
        metadata = {
            "source_batch_id": source.id, "source_page_hashes": hashes,
            "rule": "hithink_full_open_list_bounded_complement_v1",
            "source_endpoint": "/api/a-share/calendar/trading-days",
            "source_scope": "shared_a_share_calendar_mapped_to_three_exchanges",
            "source_start": str(min(opened)), "source_end": str(max(opened)),
            "start": str(start), "end": str(end),
            "observed_at": datetime.now(UTC).isoformat(),
            "derived_rows": [{"exchange": r.exchange, "trade_date": str(r.trade_date),
                              "is_open": r.is_open} for r in records],
        }
        raw = RawDailyBarImportRepository()
        with logged_job(db, job_name="calendar_bounded_completion", job_type="repair") as job:
            batch = raw.create_batch(
                db, batch_no=f"calendar_completion_{uuid4().hex[:12]}",
                source_name=source.source_name, source_type="derived",
                dataset="calendar_bounded_completion", request_params=metadata,
            )
            db.flush()
            db.add(RawVendorSnapshot(
                batch_id=batch.id, source_name=source.source_name,
                dataset="calendar_bounded_completion", snapshot_date=datetime.now(UTC).date(),
                raw_payload=metadata,
                row_hash=sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest(),
            ))
            db.flush()
            count = TradingCalendarRepository().upsert_many(
                db, records=records, source_name=source.source_name,
            )
            raw.finish_batch(db, batch, status="success", row_count=count,
                             accepted_count=count, rejected_count=0)
            job.rows_inserted = count
            job.metadata_ = {**metadata, "batch_id": batch.id}
            db.commit()
        return {"inserted": count, "batch_id": batch.id}
