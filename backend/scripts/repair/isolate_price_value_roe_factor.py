"""Archive and invalidate the legacy research ROE factor run.

Run with the API process stopped so DuckDB has a single writer.
"""

import argparse
import json
import os
from datetime import date
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.factor import FactorRun, FactorValue
from app.models.system import JobLog
from app.services.job_logging import logged_job

JOB_NAME = "price_value_research_factor_cleanup"


def cleanup_research_factor(db: Session, *, run_id: int, output_dir: Path) -> dict[str, object]:
    for job in db.scalars(
        select(JobLog).where(JobLog.job_name == JOB_NAME, JobLog.status == "success")
    ):
        if (job.metadata_ or {}).get("factor_run_id") == run_id:
            return dict(job.metadata_ or {})

    prep = next(
        (
            job
            for job in db.scalars(
                select(JobLog).where(
                    JobLog.job_name == "price_value_input_prep",
                    JobLog.status == "success",
                )
            )
            if (job.metadata_ or {}).get("factor_run_id") == run_id
        ),
        None,
    )
    run = db.get(FactorRun, run_id)
    if prep is None or run is None or run.factor_name != "roe_ttm" or run.status != "success":
        raise ValueError("run is not an active legacy price/value ROE factor")
    target = date.fromisoformat((prep.metadata_ or {})["target_trade_date"])
    values = list(
        db.scalars(select(FactorValue).where(FactorValue.run_id == run_id).order_by(FactorValue.id))
    )
    if (
        not values
        or len(values) != run.rows_written
        or any(
            row.factor_name != "roe_ttm"
            or row.factor_version != run.factor_version
            or row.trade_date != target
            for row in values
        )
    ):
        raise ValueError("factor values do not match the legacy preparation run")

    archive = {
        "factor_run_id": run_id,
        "input_prep_job_id": prep.id,
        "target_trade_date": target.isoformat(),
        "values": [
            {
                "id": row.id,
                "symbol": row.symbol,
                "trade_date": row.trade_date.isoformat(),
                "factor_name": row.factor_name,
                "factor_version": row.factor_version,
                "value": str(row.value),
                "run_id": row.run_id,
            }
            for row in values
        ],
    }
    encoded = json.dumps(archive, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = sha256(encoded).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_path = output_dir / f"research-roe-run-{run_id}-{target}.json"
    if archive_path.exists() and sha256(archive_path.read_bytes()).hexdigest() != digest:
        raise ValueError("existing factor archive has different content")
    if not archive_path.exists():
        with NamedTemporaryFile(dir=output_dir, delete=False, suffix=".tmp") as output:
            output.write(encoded)
            temporary_path = Path(output.name)
        os.replace(temporary_path, archive_path)

    metadata = {
        "factor_run_id": run_id,
        "input_prep_job_id": prep.id,
        "target_trade_date": target.isoformat(),
        "archived_rows": len(values),
        "archive_path": str(archive_path.resolve()),
        "archive_sha256": digest,
        "reason": "late research-only financial data lacked certified first-available revisions",
    }
    with logged_job(db, job_name=JOB_NAME, job_type="repair", metadata=metadata) as job:
        db.execute(delete(FactorValue).where(FactorValue.run_id == run_id))
        run.status = "failed"
        run.error_message = "Invalidated: research-only late financial observation"
        run.metadata_ = {**(run.metadata_ or {}), "invalidated_by_job_id": job.id}
        job.rows_read = len(values)
        job.rows_updated = 1
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/repair"))
    args = parser.parse_args()
    with SessionLocal() as db:
        result = cleanup_research_factor(db, run_id=args.run_id, output_dir=args.output_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
