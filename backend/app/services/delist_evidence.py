"""Persist delisting source responses to raw for later field-level certification."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.data_sources.delist_evidence import AkshareDelistSource, TushareDelistSource
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.repositories.raw import RawDailyBarImportRepository
from app.services.evidence_fallback import record_fallback_outcome, source_attempted_recently

DATASET = "delist_evidence"


def _dated_count(rows: list[dict], *, source: str, exchange: str | None) -> int:
    def valid_date(value: object) -> bool:
        if not value:
            return False
        raw = str(value)
        try:
            date.fromisoformat(
                f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if len(raw) == 8 else raw[:10]
            )
            return True
        except ValueError:
            return False

    if source == "tushare":
        return sum(
            str(row.get("ts_code", "")).endswith(f".{exchange}")
            and valid_date(row.get("delist_date"))
            for row in rows
        ) if exchange else sum(valid_date(row.get("delist_date")) for row in rows)
    if exchange == "SZ":
        return sum(valid_date(row.get("终止上市日期")) for row in rows)
    return 0  # SH supplies suspension dates, never delisting dates.


class DelistEvidenceService:
    def __init__(self, repository: RawDailyBarImportRepository | None = None) -> None:
        self.repository = repository or RawDailyBarImportRepository()
        self.sources = {"tushare": TushareDelistSource(), "akshare": AkshareDelistSource()}

    def collect(self, db: Session, *, source: str, exchange: str | None = None) -> dict:
        if source == "akshare" and exchange not in {"SH", "SZ"}:
            raise ValueError("AKShare requires exchange SH or SZ")
        adapter = self.sources[source]
        observed_at = datetime.now(UTC)
        batch = self.repository.create_batch(
            db,
            batch_no=f"delist_{source}_{observed_at:%Y%m%d%H%M%S}_{uuid4().hex[:8]}",
            source_name=adapter.name,
            source_type=adapter.source_type,
            dataset=DATASET,
            request_params={
                "endpoint": (
                    adapter.endpoint
                    if source == "tushare"
                    else f"stock_info_{exchange.lower()}_delist"
                ),
                "exchange": exchange,
                "observed_at_utc": observed_at.isoformat(),
                "publication_scope": "raw_only",
                "date_semantics": (
                    "delist_date" if source == "tushare" or exchange == "SZ"
                    else "suspension_date"
                ),
            },
        )
        db.commit()
        try:
            rows = adapter.fetch() if source == "tushare" else adapter.fetch(exchange)
            if not rows:
                raise ValueError("supplier returned empty lifecycle list")
            content = {"rows": rows, "source": source, "exchange": exchange}
            digest = sha256(
                json.dumps(
                    content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            db.add(
                RawVendorSnapshot(
                    batch_id=batch.id,
                    source_name=adapter.name,
                    dataset=DATASET,
                    snapshot_date=observed_at.date(),
                    raw_payload={**content, "observed_at_utc": observed_at.isoformat()},
                    row_hash=digest,
                )
            )
            batch.request_params = {**batch.request_params, "response_sha256": digest}
            valid_dates = _dated_count(rows, source=source, exchange=exchange)
            batch.request_params = {**batch.request_params, "valid_delist_dates": valid_dates}
            self.repository.finish_batch(
                db, batch, status="success", row_count=len(rows),
                accepted_count=len(rows), rejected_count=0,
            )
            db.commit()
            return {
                "source": source, "exchange": exchange,
                "rows": len(rows), "batch_no": batch.batch_no,
                "valid_delist_dates": valid_dates,
                "exchange_date_counts": {
                    code: _dated_count(rows, source=source, exchange=code)
                    for code in ("SH", "SZ")
                },
                "status": "success" if valid_dates else "partial",
            }
        except Exception as exc:
            db.rollback()
            failed = db.get(ImportBatch, batch.id)
            if failed:
                message = f"{type(exc).__name__}: {exc}"
                if settings.tushare_token:
                    message = message.replace(settings.tushare_token, "[REDACTED]")
                self.repository.finish_batch(
                    db, failed, status="failed", row_count=0,
                    accepted_count=0, rejected_count=0, error_message=message[:500],
                )
                db.commit()
            raise

    def collect_with_fallback(self, db: Session, *, exchange: str) -> dict:
        if exchange not in {"SH", "SZ"}:
            raise ValueError("exchange must be SH or SZ")
        attempts: list[dict] = []
        use_tushare = settings.tushare_token and not source_attempted_recently(
            db, dataset=DATASET, source_name="tushare", within=timedelta(hours=24)
        )
        sources = (["tushare"] if use_tushare else []) + ["akshare"]
        for source in sources:
            try:
                result = self.collect(db, source=source, exchange=exchange)
                attempts.append({"source": source, "status": result["status"],
                                 "batch_no": result["batch_no"]})
                if result["valid_delist_dates"]:
                    return record_fallback_outcome(db, dataset=DATASET, outcome={
                        "exchange": exchange, "status": "success",
                        "selected_source": source, "attempts": attempts,
                    })
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                if settings.tushare_token:
                    message = message.replace(settings.tushare_token, "[REDACTED]")
                attempts.append({"source": source, "status": "failed",
                                 "error": message[:500]})
        return record_fallback_outcome(db, dataset=DATASET, outcome={
            "exchange": exchange, "status": "partial" if exchange == "SH"
            and any(a["status"] == "partial" for a in attempts) else "failed",
            "selected_source": None, "attempts": attempts,
        })

    def collect_due(self, db: Session) -> list[dict]:
        """Run at most once daily; fetch the full Tushare list only once."""
        latest = db.scalar(
            select(ImportBatch).where(ImportBatch.dataset == DATASET)
            .order_by(ImportBatch.started_at.desc()).limit(1)
        )
        if latest:
            started = latest.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if started > datetime.now(UTC) - timedelta(hours=24):
                return []
        primary: dict | None = None
        primary_error: str | None = None
        if settings.tushare_token:
            try:
                primary = self.collect(db, source="tushare")
            except Exception as exc:
                primary_error = f"{type(exc).__name__}: {exc}"
                if settings.tushare_token:
                    primary_error = primary_error.replace(settings.tushare_token, "[REDACTED]")
        results: list[dict] = []
        for exchange in ("SH", "SZ"):
            if primary and primary["exchange_date_counts"][exchange]:
                results.append({"exchange": exchange, "status": "success",
                                "selected_source": "tushare",
                                "attempts": [{"source": "tushare", "status": "success",
                                              "batch_no": primary["batch_no"]}]})
            else:
                fallback = self.collect_with_fallback_without_tushare(db, exchange=exchange)
                if primary_error:
                    fallback["attempts"].insert(0, {
                        "source": "tushare", "status": "failed", "error": primary_error[:500]
                    })
                results.append(fallback)
        return [record_fallback_outcome(db, dataset=DATASET, outcome=item)
                for item in results]

    def collect_with_fallback_without_tushare(self, db: Session, *, exchange: str) -> dict:
        """Use exchange evidence after a single shared primary attempt."""
        try:
            result = self.collect(db, source="akshare", exchange=exchange)
            status = "success" if result["valid_delist_dates"] else "partial"
            return {"exchange": exchange, "status": status,
                    "selected_source": "akshare" if status == "success" else None,
                    "attempts": [{"source": "akshare", "status": status,
                                  "batch_no": result["batch_no"]}]}
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            if settings.tushare_token:
                message = message.replace(settings.tushare_token, "[REDACTED]")
            return {"exchange": exchange, "status": "failed", "selected_source": None,
                    "attempts": [{"source": "akshare", "status": "failed",
                                  "error": message[:500]}]}
