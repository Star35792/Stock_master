"""Audit turnover input coverage without publishing unverified factor values."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.data_sources.turnover_evidence import AkshareTurnoverSource, TushareTurnoverSource
from app.models.market import DailyBar
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.repositories.raw import RawDailyBarImportRepository
from app.services.evidence_fallback import record_fallback_outcome, source_attempted_recently

DATASET = "turnover_evidence"


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid supplier number") from exc
    if not number.is_finite():
        raise ValueError("non-finite supplier number")
    return number


def reconcile_turnover(
    rows: list[dict], bars: dict[tuple[str, date], Decimal], *, source: str, trade_date: date
) -> dict:
    seen: set[tuple[str, date]] = set()
    matched = mismatched = missing = invalid = 0
    for row in rows:
        if source == "tushare":
            symbol = str(row.get("ts_code") or "")
            raw_date = str(row.get("trade_date") or "")
            close = row.get("close")
            turnover = row.get("turnover_rate")
        else:
            code = str(row.get("股票代码") or "")
            symbol = next((key[0] for key in bars if key[0].startswith(code + ".")), "")
            raw_date = str(row.get("日期") or "")[:10].replace("-", "")
            close = row.get("收盘")
            turnover = row.get("换手率")
        key = (symbol, trade_date)
        if raw_date != trade_date.strftime("%Y%m%d") or key in seen:
            invalid += 1
            continue
        seen.add(key)
        try:
            observed_close = _decimal(close)
            rate = _decimal(turnover)
        except ValueError:
            invalid += 1
            continue
        if rate is None or rate < 0 or rate > 100:
            invalid += 1
        elif key not in bars:
            missing += 1
        elif observed_close is None or abs(observed_close - bars[key]) > Decimal("0.01"):
            mismatched += 1
        else:
            matched += 1
    return {
        "source_rows": len(rows),
        "matched_close_and_turnover": matched,
        "price_mismatch": mismatched,
        "missing_trusted_bar": missing,
        "invalid_or_duplicate": invalid,
        "market_bar_count": len(bars),
        "market_coverage_rate": str(Decimal(matched) / len(bars)) if bars else "0",
        "pit_verified": False,
        "publication_scope": "raw_only",
    }


class TurnoverEvidenceService:
    def __init__(self, repository: RawDailyBarImportRepository | None = None) -> None:
        self.repository = repository or RawDailyBarImportRepository()
        self.sources = {
            "tushare": TushareTurnoverSource(),
            "akshare": AkshareTurnoverSource(),
        }

    def collect_day(
        self, db: Session, *, source: str, trade_date: date, symbol: str | None = None
    ) -> dict:
        if source == "akshare" and not symbol:
            raise ValueError("AKShare requires a symbol")
        adapter = self.sources[source]
        observed_at = datetime.now(UTC)
        batch = self.repository.create_batch(
            db,
            batch_no=f"turnover_{source}_{trade_date:%Y%m%d}_{uuid4().hex[:8]}",
            source_name=adapter.name,
            source_type=adapter.source_type,
            dataset=DATASET,
            request_params={
                "endpoint": adapter.endpoint,
                "trade_date": trade_date.isoformat(),
                "symbol": symbol,
                "observed_at_utc": observed_at.isoformat(),
                "publication_scope": "raw_only",
                "pit_verified": False,
            },
        )
        db.commit()
        try:
            rows = (
                adapter.fetch(trade_date)
                if source == "tushare"
                else adapter.fetch(symbol, trade_date, trade_date)
            )
            bars = {
                (row.symbol, row.trade_date): row.close
                for row in db.execute(
                    select(DailyBar.symbol, DailyBar.trade_date, DailyBar.close).where(
                        DailyBar.trade_date == trade_date,
                        DailyBar.adjust_type == "none",
                        DailyBar.quality_status.in_(("trusted", "manual_fixed")),
                    )
                ).all()
            }
            if symbol:
                bars = {key: close for key, close in bars.items() if key[0] == symbol}
            report = reconcile_turnover(rows, bars, source=source, trade_date=trade_date)
            content = {"rows": rows, "trade_date": trade_date.isoformat(), "symbol": symbol}
            digest = sha256(
                json.dumps(
                    content, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
            db.add(
                RawVendorSnapshot(
                    batch_id=batch.id,
                    source_name=adapter.name,
                    dataset=DATASET,
                    snapshot_date=trade_date,
                    raw_payload={
                        **content,
                        "observed_at_utc": observed_at.isoformat(),
                        "reconciliation": report,
                    },
                    row_hash=digest,
                )
            )
            batch.request_params = {**batch.request_params, "response_sha256": digest, **report}
            self.repository.finish_batch(
                db,
                batch,
                status="success",
                row_count=len(rows),
                accepted_count=len(rows),
                rejected_count=0,
            )
            db.commit()
            return {
                "batch_no": batch.batch_no,
                "source": source,
                "date": trade_date.isoformat(),
                **report,
            }
        except Exception as exc:
            db.rollback()
            failed = db.get(ImportBatch, batch.id)
            if failed:
                message = f"{type(exc).__name__}: {exc}"
                if settings.tushare_token:
                    message = message.replace(settings.tushare_token, "[REDACTED]")
                self.repository.finish_batch(
                    db, failed, status="failed", row_count=0, accepted_count=0,
                    rejected_count=0, error_message=message[:500],
                )
                db.commit()
            raise

    def collect_with_fallback(
        self, db: Session, *, trade_date: date, symbol: str,
        try_tushare: bool = True,
    ) -> dict:
        """A same-symbol turnover fallback, accepted only after price reconciliation."""
        attempts: list[dict] = []
        use_tushare = (try_tushare and settings.tushare_token and not
                       source_attempted_recently(
                           db, dataset=DATASET, source_name="tushare",
                           within=timedelta(minutes=65),
                       ))
        sources = (["tushare"] if use_tushare else []) + [
            "akshare"
        ]
        for source in sources:
            try:
                result = self.collect_day(
                    db, source=source, trade_date=trade_date, symbol=symbol
                )
                matched = result["matched_close_and_turnover"] > 0
                attempts.append({"source": source,
                                 "status": "matched" if matched else "unmatched",
                                 "batch_no": result["batch_no"]})
                if matched:
                    return record_fallback_outcome(db, dataset=DATASET, outcome={
                        "symbol": symbol, "date": trade_date.isoformat(),
                        "status": "success", "selected_source": source,
                        "attempts": attempts, "publication_scope": "raw_only",
                    })
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                if settings.tushare_token:
                    message = message.replace(settings.tushare_token, "[REDACTED]")
                attempts.append({"source": source, "status": "failed",
                                 "error": message[:500]})
        return record_fallback_outcome(db, dataset=DATASET, outcome={
            "symbol": symbol, "date": trade_date.isoformat(),
            "status": "failed", "selected_source": None,
            "attempts": attempts, "publication_scope": "raw_only",
        })

    def collect_pending(
        self, db: Session, *, trade_date: date, limit: int = 20
    ) -> list[dict]:
        """Bounded hourly collection with a global Tushare cooldown."""
        if limit <= 0:
            return []
        symbols = db.scalars(
            select(DailyBar.symbol).where(
                DailyBar.trade_date == trade_date,
                DailyBar.adjust_type == "none",
                DailyBar.quality_status.in_(("trusted", "manual_fixed")),
                DailyBar.turnover_rate.is_(None),
            ).order_by(DailyBar.symbol)
        ).all()
        attempts = db.scalars(
            select(ImportBatch).where(ImportBatch.dataset == DATASET)
            .order_by(ImportBatch.started_at.desc())
        ).all()
        date_key = trade_date.isoformat()
        last_akshare: dict[str, datetime] = {}
        for batch in attempts:
            params = batch.request_params or {}
            if params.get("trade_date") != date_key:
                continue
            if batch.source_name == "akshare_eastmoney":
                symbol = params.get("symbol")
                if symbol and symbol not in last_akshare:
                    last_akshare[symbol] = batch.started_at
        selected = sorted(
            set(symbols),
            key=lambda symbol: (
                symbol in last_akshare,
                str(last_akshare.get(symbol, "")), symbol,
            ),
        )[:limit]
        return [
            self.collect_with_fallback(
                db, trade_date=trade_date, symbol=symbol,
                try_tushare=index == 0,
            )
            for index, symbol in enumerate(selected)
        ]
