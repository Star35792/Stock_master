"""Shrink the control plane after an interrupted valuation-history sweep.

Why this script exists
----------------------
An early version of the valuation-history sweep stored the *whole* supplier
response body in ``raw.vendor_snapshot.raw_payload``.  Each page carries
roughly 1.6 MB, so an interrupted 100-page sweep left about 165 MB of JSON in
the control plane and pushed the database file from 726 MB past 1 GB.  The
current collector only records page-level *metadata* (request parameters,
response hash, row count, fetch time), so the leftover bodies are dead weight.

The script is idempotent and conservative:

* it only rewrites rows that still carry a ``response_json`` body,
* it keeps the response SHA256 (``row_hash``) and the page metadata, so the
  sweep stays auditable and verifiable,
* it never touches the rows written by the current collector,
* it converges ``running`` batches that have no live owner into ``failed``,
  because a batch left in ``running`` forever misrepresents the pipeline state.

Run with ``--dry-run`` first to see what would change.
"""

from __future__ import annotations

import argparse
import json

from sqlalchemy import text

from app.database.session import SessionLocal, initialize_database

#: Only this dataset is affected by the full-body legacy write.
TARGET_DATASET = "valuation_history_research"

#: The key that holds the legacy full response body.
LEGACY_BODY_KEY = "response_json"


def _strip_body(payload: object) -> tuple[object, bool]:
    """Return ``(new_payload, changed)`` with the legacy body removed."""
    if not isinstance(payload, dict):
        return payload, False
    if LEGACY_BODY_KEY not in payload:
        return payload, False
    slim = {key: value for key, value in payload.items() if key != LEGACY_BODY_KEY}
    slim["body_stripped"] = True
    slim["body_stripped_reason"] = (
        "full supplier response body removed from the control plane; "
        "the response SHA256 is preserved in row_hash"
    )
    return slim, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    args = parser.parse_args()

    initialize_database()

    with SessionLocal() as db:
        rows = db.execute(
            text(
                """
                SELECT id, cast(raw_payload AS VARCHAR)
                FROM raw.vendor_snapshot
                WHERE dataset = :dataset
                ORDER BY id
                """
            ),
            {"dataset": TARGET_DATASET},
        ).fetchall()

        to_update: list[tuple[str, int, str]] = []
        total_bytes = 0
        for row_id, payload_text in rows:
            if payload_text is None:
                continue
            try:
                payload = json.loads(payload_text)
            except (TypeError, ValueError):
                continue
            new_payload, changed = _strip_body(payload)
            if not changed:
                continue
            total_bytes += len(payload_text.encode("utf-8"))
            to_update.append((json.dumps(new_payload, ensure_ascii=False), row_id, payload_text))

        print(f"rows inspected : {len(rows)}")
        print(f"rows to shrink : {len(to_update)}")
        print(f"bytes released : {total_bytes / 1024 / 1024:.1f} MB (payload only)")

        stale = db.execute(
            text(
                """
                SELECT id, batch_no
                FROM raw.import_batch
                WHERE status = 'running'
                  AND dataset = :dataset
                ORDER BY id
                """
            ),
            {"dataset": TARGET_DATASET},
        ).fetchall()
        print(f"stale running batches: {len(stale)}")
        for batch_id, batch_no in stale:
            print(f"  - #{batch_id} {batch_no}")

        if args.dry_run:
            print("dry run: nothing written")
            return 0

        for new_text, row_id, _old in to_update:
            db.execute(
                text(
                    """
                    UPDATE raw.vendor_snapshot
                    SET raw_payload = CAST(:payload AS JSON)
                    WHERE id = :id
                    """
                ),
                {"payload": new_text, "id": row_id},
            )

        for batch_id, _batch_no in stale:
            db.execute(
                text(
                    """
                    UPDATE raw.import_batch
                    SET status = 'failed',
                        finished_at = now(),
                        error_message = coalesce(
                            error_message,
                            'sweep interrupted before publication; '
                            'no release was published'
                        )
                    WHERE id = :id
                    """
                ),
                {"id": batch_id},
            )

        db.commit()
        print(f"updated {len(to_update)} evidence rows and {len(stale)} batches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
