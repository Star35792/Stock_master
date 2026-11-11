"""Audit current-name ST derivation for the latest completed hot-window day.

Current research observation only; historical ST status is not certified.
Only NULL unadjusted stock bar statuses are filled; existing values are retained.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.market.special_treatment import derive_special_treatment_status  # noqa: E402
from app.models.raw import ImportBatch, RawVendorSnapshot  # noqa: E402
from app.repositories.raw import RawDailyBarImportRepository  # noqa: E402
from app.services.job_logging import logged_job  # noqa: E402
from app.utils.duckdb_write_lock import duckdb_write_lock  # noqa: E402

DATASET = "current_name_special_treatment"
SOURCE = "security_name_current"


def backfill(db: Session, *, target: date, observed_at: datetime, dry_run: bool = False):
    if observed_at.tzinfo is None:
        raise ValueError("observation clock must include timezone")
    today = observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    latest = db.scalar(text(
        "SELECT max(trade_date) FROM market.trading_calendar "
        "WHERE is_open AND trade_date < :today"
    ), {"today": today})
    if target != latest:
        raise ValueError("only the latest completed trading day may use current-name status")
    rows = db.execute(text(
        "SELECT b.id, b.symbol, b.trade_date, b.source_name AS bar_source, "
        "b.special_treatment_status AS previous_status, s.name, s.source_name, "
        "s.updated_at, s.special_treatment_status AS security_status "
        "FROM market.daily_bar b JOIN market.security s ON s.symbol=b.symbol "
        "WHERE b.trade_date=:target AND b.adjust_type='none' "
        "AND b.special_treatment_status IS NULL AND s.security_type='stock' "
        "ORDER BY b.symbol"
    ), {"target": target}).mappings().all()
    evidence = []
    for row in rows:
        status = derive_special_treatment_status(row["name"])
        if status is None:
            continue
        evidence.append({
            **dict(row), "derived_status": status,
            "observed_at": observed_at.astimezone(UTC).isoformat(),
            "observation_mode": "current_research_catchup",
            "pit_verified": False, "historical_status_verified": False,
        })
    metadata = {
        "target_trade_date": target.isoformat(),
        "observed_at": observed_at.astimezone(UTC).isoformat(),
        "source": SOURCE, "code_version": settings.app_code_version,
        "observation_mode": "current_research_catchup", "pit_verified": False,
        "historical_status_verified": False,
        "eligible_count": len(evidence), "missing_name_count": len(rows) - len(evidence),
        "derived_counts": dict(Counter(row["derived_status"] for row in evidence)),
    }
    if dry_run:
        return {**metadata, "dry_run": True}
    repo = RawDailyBarImportRepository()
    with logged_job(db, job_name="backfill_current_special_treatment", job_type="repair",
                    metadata=metadata) as job:
        batch = repo.create_batch(
            db, batch_no=f"current_st_{target:%Y%m%d}_{uuid4().hex[:12]}",
            source_name=SOURCE, source_type="derived", dataset=DATASET,
            request_params=metadata,
        )
        db.flush()
        job.batch_id = batch.id
        for row in evidence:
            payload = json.loads(json.dumps(row, default=str))
            db.add(RawVendorSnapshot(
                batch_id=batch.id, source_name=SOURCE, dataset=DATASET,
                snapshot_date=target, raw_payload=payload,
                row_hash=sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            ))
        db.commit()  # Preserve input names and old bar values before any update.
        try:
            if evidence:
                db.execute(text(
                    "UPDATE market.daily_bar SET special_treatment_status=:status "
                    "WHERE id=:id AND trade_date=:target AND adjust_type='none' "
                    "AND special_treatment_status IS NULL"
                ), [{"status": row["derived_status"], "id": row["id"], "target": target}
                    for row in evidence])
            repo.finish_batch(db, batch, status="success", row_count=len(rows),
                              accepted_count=len(evidence),
                              rejected_count=len(rows) - len(evidence))
            job.rows_read = len(rows)
            job.rows_updated = len(evidence)
            job.metadata_ = {**metadata, "batch_id": batch.id, "batch_no": batch.batch_no}
        except Exception as exc:
            db.rollback()
            batch = db.get(ImportBatch, batch.id)
            repo.finish_batch(db, batch, status="failed", row_count=len(rows),
                              accepted_count=0, rejected_count=len(rows),
                              error_message=type(exc).__name__)
            db.commit()
            raise
    return dict(job.metadata_)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.start_date != args.end_date:
        parser.error("current-name backfill requires one explicit trading day")
    lock_path = Path(settings.metadata_db_path).resolve().parent / "duckdb_write.lock"
    with duckdb_write_lock(lock_path):
        # Importing app.database.session would initialize schema as a side effect.
        engine = create_engine(f"duckdb:///{settings.metadata_db_path}")
        try:
            with Session(engine) as db:
                result = backfill(db, target=args.start_date,
                                  observed_at=datetime.now(UTC), dry_run=args.dry_run)
        finally:
            engine.dispose()
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
