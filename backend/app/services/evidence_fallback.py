"""Persist the decision made after supplier evidence attempts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.raw import ImportBatch


def source_attempted_recently(
    db: Session, *, dataset: str, source_name: str, within: timedelta
) -> bool:
    latest = db.scalar(
        select(ImportBatch.started_at).where(
            ImportBatch.dataset == dataset, ImportBatch.source_name == source_name
        ).order_by(ImportBatch.started_at.desc()).limit(1)
    )
    if latest is None:
        return False
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    return latest > datetime.now(UTC) - within


def record_fallback_outcome(db: Session, *, dataset: str, outcome: dict) -> dict:
    attempts = outcome["attempts"]
    batch_no = next((item["batch_no"] for item in reversed(attempts)
                     if item.get("batch_no")), None)
    if batch_no:
        batch = db.scalar(select(ImportBatch).where(ImportBatch.batch_no == batch_no))
    else:
        candidates = db.scalars(
            select(ImportBatch).where(ImportBatch.dataset == dataset)
            .order_by(ImportBatch.started_at.desc(), ImportBatch.id.desc()).limit(100)
        ).all()
        batch = next((candidate for candidate in candidates
                      if all((candidate.request_params or {}).get(key) == outcome[key]
                             for key in ("symbol", "exchange") if key in outcome)), None)
    if batch is not None:
        decision = {
            "status": outcome["status"],
            "selected_source": outcome.get("selected_source"),
            "attempts": [
                {key: item[key] for key in ("source", "status", "batch_no")
                 if key in item}
                for item in attempts
            ],
        }
        params = batch.request_params or {}
        if outcome.get("exchange"):
            decisions = {**params.get("fallback_outcomes", {})}
            decisions[outcome["exchange"]] = decision
            batch.request_params = {**params, "fallback_outcomes": decisions}
        else:
            batch.request_params = {**params, "fallback_outcome": decision}
        db.commit()
    return outcome
