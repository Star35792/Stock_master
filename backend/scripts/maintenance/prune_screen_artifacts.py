"""Prune superseded price/value screen artifacts and keep one report + cohort.

Why this script exists
----------------------
``PriceValueScreenService`` prunes old artifacts only when a new screen run
executes.  When the screen stops running, every past report stays on disk as
a multi-hundred-megabyte pile of JSON.  This script performs the same
retirement explicitly and safely:

* the latest job's report stays on disk;
* the frozen cohort is (re)derived from that report with the application's own
  ``build_cohort`` so the report/cohort pair stays consistent;
* every older job keeps its summary fields (status, counts, ``snapshot_hash``)
  but loses the path reference to a file that no longer exists;
* only after the references are retired are the superseded files removed.

Usage
-----
    python -m scripts.maintenance.prune_screen_artifacts --dry-run
    python -m scripts.maintenance.prune_screen_artifacts
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.database.session import SessionLocal  # noqa: E402
from app.repositories.strategy.price_value_screen import (  # noqa: E402
    PriceValueScreenRepository,
)
from app.services.price_value_screen import _SCREEN_ARTIFACT_NAME  # noqa: E402
from app.services.price_value_tracking import build_cohort  # noqa: E402
from app.utils.duckdb_write_lock import duckdb_write_lock  # noqa: E402

BATCH = 30


def _write_cohort(output_dir: Path, report: dict) -> str:
    """Write the frozen cohort next to the report, mirroring the service."""
    cohort_name = (
        f"{report['target_trade_date'] or 'no-calendar'}-"
        f"{report['snapshot_hash'][:20]}.cohort.json"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output_dir,
        delete=False,
        prefix=".price-value-cohort-",
        suffix=".tmp",
    ) as output:
        json.dump(build_cohort(report), output, ensure_ascii=False, separators=(",", ":"))
        temporary_name = output.name
    os.replace(temporary_name, output_dir / cohort_name)
    return cohort_name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    repository = PriceValueScreenRepository()
    with duckdb_write_lock():
        with SessionLocal() as db:
            job = repository.latest_screen_job(db)
            if job is None:
                print("no price_value_screen job found; nothing to prune")
                return 1
            metadata = dict(job.metadata_ or {})
            artifact_name = metadata.get("artifact_name")
            if not artifact_name:
                print(f"latest job {job.id} has no artifact_name; nothing to prune")
                return 1
            output_dir = Path("data/price_value_screens")
            report_path = output_dir / artifact_name
            if not report_path.is_file():
                print(f"latest artifact missing on disk: {report_path}")
                return 1
            report = json.loads(report_path.read_text(encoding="utf-8"))

            cohort_name = metadata.get("cohort_name")
            cohort_path = output_dir / cohort_name if cohort_name else None
            if cohort_path is None or not cohort_path.is_file():
                if args.dry_run:
                    print(f"[dry-run] would derive cohort for {artifact_name}")
                else:
                    cohort_name = _write_cohort(output_dir, report)
                    metadata = {**metadata, "cohort_name": cohort_name}
                    job.metadata_ = metadata
                    db.commit()
                    print(f"derived cohort: {cohort_name}")

            keep = {artifact_name, cohort_name}
            stale = [
                path
                for path in sorted(output_dir.iterdir())
                if path.is_file()
                and path.name not in keep
                and _SCREEN_ARTIFACT_NAME.fullmatch(path.name)
            ]
            reclaimed = sum(path.stat().st_size for path in stale)
            print(
                f"superseded artifacts: {len(stale)} "
                f"({reclaimed / 1024 / 1024:.1f} MiB)"
            )
            if args.dry_run:
                for path in stale[:5]:
                    print(f"  [dry-run] would remove {path.name}")
                if len(stale) > 5:
                    print(f"  ... and {len(stale) - 5} more")
                return 0

            retired = repository.retire_screen_artifact_references(db, keep_job_id=job.id)
            db.commit()
            print(f"retired artifact references on {retired} historical job(s)")

            for index in range(0, len(stale), BATCH):
                for path in stale[index : index + BATCH]:
                    path.unlink()
            print(f"removed {len(stale)} superseded artifact(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
