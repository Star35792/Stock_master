"""Close exact running job logs that automatic lease recovery will not handle."""

from __future__ import annotations

import argparse

from app.database.session import SessionLocal, initialize_database
from app.services.job_lease import describe_running_jobs, reconcile_legacy_running_jobs
from app.utils.duckdb_write_lock import duckdb_write_lock


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list",
        action="store_true",
        help="Read-only report of every running row and how it can be closed.",
    )
    parser.add_argument(
        "--job-ids",
        nargs="+",
        type=int,
        help="Exact metadata.job_log IDs to review.",
    )
    parser.add_argument(
        "--reason",
        help="Operator-confirmed reason recorded in audit metadata.",
    )
    parser.add_argument(
        "--operator",
        help="Operator name recorded in audit metadata.",
    )
    parser.add_argument(
        "--confirm-owner-exited",
        action="store_true",
        help=(
            "Also accept rows that already carry lease metadata, which is only valid when "
            "their slot guard is missing or its lease metadata is unverifiable."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the reconciliation. Omit for the default dry run.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    with duckdb_write_lock(timeout_seconds=0):
        initialize_database()
        with SessionLocal() as db:
            if args.list:
                states = describe_running_jobs(db)
                if not states:
                    print("running job logs: none")
                    return 0
                print(f"running job logs: {len(states)}")
                for state in states:
                    slot = "-" if state.slot is None else state.slot
                    print(
                        f"  #{state.job_id} {state.job_name} started_at={state.started_at} "
                        f"state={state.state} slot={slot}"
                    )
                return 0
            if not (args.job_ids and args.reason and args.operator):
                parser.error("--job-ids, --reason and --operator are required without --list")
            jobs = reconcile_legacy_running_jobs(
                db,
                job_ids=args.job_ids,
                reason=args.reason,
                operator=args.operator,
                apply=args.apply,
                confirm_owner_exited=args.confirm_owner_exited,
            )
            mode = "applied" if args.apply else "dry-run"
            print(f"running job reconciliation: {mode}")
            for job in jobs:
                print(
                    f"  #{job.id} {job.job_name} started_at={job.started_at} "
                    "completion_status=unknown"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
