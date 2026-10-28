"""Collect corporate-action evidence from independent suppliers into raw only."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.data_sources.corporate_action_evidence import (
    AkshareCorporateActionSource,
    CorporateActionResponse,
    EastmoneyCorporateActionSource,
    TushareCorporateActionSource,
)
from app.models.market import CorporateAction
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.repositories.raw import RawDailyBarImportRepository
from app.services.evidence_fallback import record_fallback_outcome, source_attempted_recently

DATASET = "corporate_action_evidence"
_SYMBOL = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DATE_FIELDS = {
    "tushare": ("record_date", "ex_date", "pay_date"),
    "akshare": ("股权登记日", "除权日", "派息日"),
    "eastmoney": ("股权登记日", "除权除息日", None),
}


def _parsed_date(value: object) -> date | None:
    if not value:
        return None
    raw = str(value)
    try:
        return date.fromisoformat(
            f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if re.fullmatch(r"\d{8}", raw)
            else raw[:10]
        )
    except ValueError:
        return None


def _evidence_coverage(
    rows: list[dict], source: str, target_ex_dates: set[date] | None = None
) -> dict[str, int | bool]:
    record_key, ex_key, pay_key = _DATE_FIELDS[source]
    relevant = [row for row in rows if target_ex_dates is None
                or _parsed_date(row.get(ex_key)) in target_ex_dates]
    dated = sum(bool(_parsed_date(row.get(record_key)) and _parsed_date(row.get(ex_key)))
                for row in relevant)
    complete = sum(
        bool(_parsed_date(row.get(record_key)) and _parsed_date(row.get(ex_key))
             and pay_key and _parsed_date(row.get(pay_key)))
        for row in relevant
    )
    complete_dates = {
        _parsed_date(row.get(ex_key)) for row in relevant
        if _parsed_date(row.get(record_key)) and pay_key
        and _parsed_date(row.get(pay_key))
    }
    return {"dated_rows": dated, "complete_rows": complete,
            "payment_date_supported": pay_key is not None,
            "target_ex_dates": len(target_ex_dates) if target_ex_dates is not None else 0,
            "all_targets_complete": (
                target_ex_dates.issubset(complete_dates) if target_ex_dates is not None
                else complete > 0
            )}


def _safe_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}"
    if settings.tushare_token:
        message = message.replace(settings.tushare_token, "[REDACTED]")
    return message[:500]


class CorporateActionEvidenceService:
    def __init__(self, *, raw_repository: RawDailyBarImportRepository | None = None) -> None:
        self.raw_repository = raw_repository or RawDailyBarImportRepository()
        self.sources = {
            "akshare": AkshareCorporateActionSource(),
            "tushare": TushareCorporateActionSource(),
            "eastmoney": EastmoneyCorporateActionSource(),
        }

    def collect_one(
        self, db: Session, *, source: str, symbol: str,
        target_ex_dates: set[date] | None = None,
    ) -> dict:
        symbol = symbol.strip().upper()
        if not _SYMBOL.fullmatch(symbol):
            raise ValueError("symbol must be a six-digit A-share code with exchange suffix")
        adapter = self.sources[source]
        observed_at = datetime.now(UTC)
        batch = self.raw_repository.create_batch(
            db,
            batch_no=f"ca_{source}_{observed_at:%Y%m%d%H%M%S}_{uuid4().hex[:8]}",
            source_name=adapter.name,
            source_type=adapter.source_type,
            dataset=DATASET,
            request_params={
                "endpoint": adapter.endpoint,
                "symbol": symbol,
                "observed_at_utc": observed_at.isoformat(),
                "publication_scope": "raw_only",
                "pit_verified": False,
            },
        )
        db.commit()  # Persist the attempt before the external request.
        try:
            response: CorporateActionResponse = adapter.fetch(symbol)
            if not isinstance(response.rows, list) or any(
                not isinstance(row, dict) for row in response.rows
            ):
                raise ValueError("supplier response is not a list of records")
            content = {"symbol": symbol, "endpoint": response.endpoint, "rows": response.rows}
            coverage = _evidence_coverage(response.rows, source, target_ex_dates)
            digest = sha256(
                json.dumps(
                    content, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
            existing = db.scalar(
                select(RawVendorSnapshot.id)
                .where(
                    RawVendorSnapshot.source_name == adapter.name,
                    RawVendorSnapshot.dataset == DATASET,
                    RawVendorSnapshot.row_hash == digest,
                )
                .limit(1)
            )
            inserted = existing is None
            if inserted:
                db.add(
                    RawVendorSnapshot(
                        batch_id=batch.id,
                        source_name=adapter.name,
                        dataset=DATASET,
                        snapshot_date=observed_at.astimezone(_SHANGHAI).date(),
                        raw_payload={
                            **content,
                            "source_version": response.source_version,
                            "observed_at_utc": observed_at.isoformat(),
                            "pit_verified": False,
                        },
                        row_hash=digest,
                    )
                )
            batch.request_params = {
                **(batch.request_params or {}),
                "response_sha256": digest,
                "source_version": response.source_version,
                "unchanged": not inserted,
                "evidence_coverage": coverage,
            }
            self.raw_repository.finish_batch(
                db,
                batch,
                status="success",
                row_count=len(response.rows),
                accepted_count=len(response.rows) if inserted else 0,
                rejected_count=0,
            )
            db.commit()
            evidence_status = (
                "success" if coverage["all_targets_complete"] else
                "partial" if coverage["dated_rows"] else "empty"
            )
            return {
                "source": source,
                "symbol": symbol,
                "status": evidence_status,
                "rows": len(response.rows),
                "evidence_coverage": coverage,
                "new_snapshot": inserted,
                "batch_no": batch.batch_no,
            }
        except Exception as exc:
            db.rollback()
            failed = db.get(ImportBatch, batch.id)
            if failed is not None:
                self.raw_repository.finish_batch(
                    db,
                    failed,
                    status="failed",
                    row_count=0,
                    accepted_count=0,
                    rejected_count=0,
                    error_message=_safe_error(exc),
                )
                db.commit()
            raise RuntimeError(_safe_error(exc)) from None

    def collect_with_fallback(
        self, db: Session, *, symbol: str, try_tushare: bool = True
    ) -> dict:
        """Try compatible suppliers in order, retaining every attempt in raw."""
        use_tushare = (try_tushare and settings.tushare_token and not
                       source_attempted_recently(
                           db, dataset=DATASET, source_name="tushare",
                           within=timedelta(minutes=65),
                       ))
        sources = (["tushare"] if use_tushare else []) + [
            "akshare", "eastmoney"
        ]
        target_events = db.scalars(
            select(CorporateAction.ex_date).where(
                CorporateAction.symbol == symbol,
                or_(CorporateAction.record_date.is_(None),
                    CorporateAction.payment_date.is_(None)),
            )
        ).all()
        target_ex_dates = set(target_events)
        target_ex_dates.discard(None)
        attempts: list[dict] = []
        best_partial: dict | None = None
        for source in sources:
            try:
                result = self.collect_one(
                    db, source=source, symbol=symbol,
                    target_ex_dates=target_ex_dates if target_events else None,
                )
            except Exception as exc:
                attempts.append({"source": source, "status": "failed", "error": _safe_error(exc)})
                continue
            coverage = result["evidence_coverage"]
            status = "complete" if coverage["all_targets_complete"] else (
                "partial" if coverage["dated_rows"] else "empty"
            )
            attempts.append({"source": source, "status": status,
                             "batch_no": result["batch_no"]})
            if status == "complete":
                return record_fallback_outcome(db, dataset=DATASET, outcome={
                    "symbol": symbol, "status": "success", "selected_source": source,
                    "attempts": attempts,
                })
            if status == "partial" and best_partial is None:
                best_partial = result
        return record_fallback_outcome(db, dataset=DATASET, outcome={
            "symbol": symbol,
            "status": "partial" if best_partial else "failed",
            "selected_source": best_partial["source"] if best_partial else None,
            "attempts": attempts,
        })

    def pending_symbols(self, db: Session, *, source: str, limit: int) -> list[str]:
        if limit <= 0:
            return []
        adapter = self.sources[source]
        attempts = db.scalars(
            select(ImportBatch)
            .where(ImportBatch.source_name == adapter.name, ImportBatch.dataset == DATASET)
            .order_by(ImportBatch.started_at.desc())
        ).all()
        last_by_symbol: dict[str, datetime] = {}
        for batch in attempts:
            symbol = (batch.request_params or {}).get("symbol")
            if symbol and symbol not in last_by_symbol:
                last_by_symbol[symbol] = batch.started_at
        if source == "tushare" and attempts:
            latest = attempts[0].started_at
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=UTC)
            if latest > datetime.now(UTC) - timedelta(minutes=65):
                return []
        symbols = db.scalars(
            select(CorporateAction.symbol)
            .where(
                or_(
                    CorporateAction.record_date.is_(None),
                    CorporateAction.payment_date.is_(None),
                )
            )
            .distinct()
            .order_by(CorporateAction.symbol)
        ).all()
        eligible = [symbol for symbol in symbols if _SYMBOL.fullmatch(symbol)]
        return sorted(
            eligible,
            key=lambda symbol: (
                symbol in last_by_symbol,
                str(last_by_symbol.get(symbol, "")),
                symbol,
            ),
        )[:limit]

    def collect_pending(self, db: Session) -> list[dict]:
        results: list[dict] = []
        symbols = self.pending_symbols(
            db, source="akshare", limit=settings.corporate_action_akshare_symbols_per_run
        )
        tushare_candidates: list[str] = []
        if settings.tushare_token:
            tushare_candidates = self.pending_symbols(
                db, source="tushare",
                limit=settings.corporate_action_tushare_symbols_per_run,
            )
        tushare_eligible = set(tushare_candidates)
        symbols = list(dict.fromkeys([*tushare_candidates, *symbols]))[:
            max(settings.corporate_action_akshare_symbols_per_run,
                len(tushare_candidates))
        ]
        for symbol in symbols:
            results.append(self.collect_with_fallback(
                db, symbol=symbol, try_tushare=symbol in tushare_eligible
            ))
        return results
