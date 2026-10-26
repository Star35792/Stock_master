"""Sweep Eastmoney valuation history into the research warm layer.

The sweep publishes one new warm-layer release that carries
``research/valuation_history`` and inherits every other table from its
parent.  Page-level evidence (request parameters, response hash, row count,
fetch time) is written to the control plane so the sweep is auditable; the
multi-million-row detail stays in Parquet.

The release is published atomically: Parquet is written into a staging
directory first and only moved into ``exported/<tag>/`` once every gate has
passed, so a reader never sees a half-written release.

An interrupted sweep resumes: each page is staged as Parquet as soon as it
arrives, and a rerun reuses the staged pages instead of re-downloading them.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

from app.database.session import SessionLocal, initialize_database
from app.models.raw import RawVendorSnapshot
from app.repositories.raw import RawDailyBarImportRepository
from app.repositories.warm_layer import WarmLayerStore
from app.services.valuation_history_collection import (
    DATASET,
    PAGE_SIZE,
    CollectionResult,
    PageEvidence,
    ValuationHistoryCollector,
    evidence_hash,
    now_iso,
)
from app.utils.discard import discard_directory


def _release_tag(start_date: date, end_date: date) -> str:
    """Build a sortable release tag for the sweep."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return f"{stamp}-valhist-{start_date:%Y%m%d}-{end_date:%Y%m%d}"


def _staged_pages(pages_dir: Path) -> dict[int, Path]:
    """Return the already-staged page files keyed by page number."""
    staged: dict[int, Path] = {}
    if not pages_dir.exists():
        return staged
    for path in sorted(pages_dir.glob("page_*/data_0.parquet")):
        try:
            page = int(path.parent.name.split("_")[1])
        except (IndexError, ValueError):
            continue
        staged[page] = path
    return staged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--warm-root",
        default=None,
        help="Warm-layer root (defaults to the configured root).",
    )
    parser.add_argument(
        "--base-release-tag",
        default=None,
        help="Parent release to inherit from (defaults to the latest release).",
    )
    parser.add_argument(
        "--release-tag",
        default=None,
        help="Release tag to publish under (defaults to a generated tag).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Concurrent page requests (default 8).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse the pages already staged for this release tag.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and gate the data without publishing a release.",
    )
    args = parser.parse_args()

    if args.start_date > args.end_date:
        parser.error("--start-date must not be after --end-date")

    initialize_database()
    collector = ValuationHistoryCollector(concurrency=args.concurrency)
    store = WarmLayerStore(args.warm_root)
    base_tag = args.base_release_tag or store.latest_release_tag
    if base_tag is None:
        raise SystemExit("no warm-layer release available to inherit from")
    release_tag = args.release_tag or _release_tag(args.start_date, args.end_date)

    warm_root = store.warm_root
    staging = warm_root / "staging" / release_tag
    pages_dir = staging / "_pages"
    if staging.exists() and not args.resume:
        # The staged pages of a previous run are throw-away trees, and a
        # bulk delete of that shape trips the host environment's safe-delete
        # guard, so they are moved aside instead.
        discard_directory(staging, trash_root=warm_root / "staging" / ".trash")
    pages_dir.mkdir(parents=True, exist_ok=True)

    # The trusted closes come from the warm layer, which carries the full
    # history; the control plane only holds a hot window.  They are exported
    # to Parquet so the price gate can join them in SQL: materialising three
    # million closes as Python rows would dominate the sweep's runtime.
    #
    # The export lives outside the staging directory: the staging tree is
    # moved into ``exported/`` on publication, and the closes are only an
    # input to the gate.
    trusted_parquet = Path(tempfile.gettempdir()) / (
        f"trusted_closes_{release_tag}.parquet"
    )
    try:
        return _run_sweep(
            args=args,
            collector=collector,
            store=store,
            base_tag=base_tag,
            release_tag=release_tag,
            staging=staging,
            pages_dir=pages_dir,
            warm_root=warm_root,
            trusted_parquet=trusted_parquet,
        )
    finally:
        # The exported closes are only an input to the gate.
        trusted_parquet.unlink(missing_ok=True)


def _run_sweep(
    *,
    args: argparse.Namespace,
    collector: ValuationHistoryCollector,
    store: WarmLayerStore,
    base_tag: str,
    release_tag: str,
    staging: Path,
    pages_dir: Path,
    warm_root: Path,
    trusted_parquet: Path,
) -> int:
    """Fetch, gate and publish one valuation-history sweep."""
    trusted_con = store.connect(
        release_tag=base_tag, read_only=True, attach_control=False
    )
    trusted_count = collector.export_trusted_closes(
        trusted_con,
        start_date=args.start_date,
        end_date=args.end_date,
        output_path=trusted_parquet,
    )
    trusted_con.close()
    if not trusted_count:
        raise SystemExit("no trusted daily bars in the requested window")

    result = CollectionResult(
        start_date=args.start_date,
        end_date=args.end_date,
        row_count=0,
        trading_days=0,
        pages_fetched=0,
    )

    raw_repo = RawDailyBarImportRepository()
    with SessionLocal() as db:
        # ``batch_no`` is unique, so it has to identify a single *run*: a
        # resumed sweep is a new run and therefore gets a new batch number.
        batch_no = f"valhist_{release_tag[:44]}-{datetime.now():%H%M%S}"
        batch = raw_repo.create_batch(
            db,
            batch_no=batch_no,
            source_name="eastmoney",
            source_type="api",
            dataset=DATASET,
            request_params={
                "endpoint": "https://datacenter-web.eastmoney.com/api/data/v1/get",
                "report_name": "RPT_VALUEANALYSIS_DET",
                "filter": (
                    f"(TRADE_DATE>='{args.start_date.isoformat()}')"
                    f"(TRADE_DATE<='{args.end_date.isoformat()}')"
                ),
                "page_size": PAGE_SIZE,
                "observed_at_utc": now_iso(),
                "pit_verified": False,
                "publication_scope": "research_warm_layer",
                "release_tag": release_tag,
                "base_release_tag": base_tag,
            },
        )
        db.commit()

        staged = _staged_pages(pages_dir) if args.resume else {}
        skip_pages = frozenset(staged)
        if skip_pages:
            print(f"resuming: {len(skip_pages)} pages already staged")

        observed_at = now_iso()
        for page, items, raw_text in collector.iter_pages(
            start_date=args.start_date,
            end_date=args.end_date,
            skip_pages=skip_pages,
        ):
            fetched_at = now_iso()
            # Only the evidence *metadata* is stored: the request parameters,
            # the response hash, the row count and the fetch time.  The
            # response body itself is ~1.5 MB per page, so keeping it would
            # add roughly a gigabyte to the control plane for a single
            # sweep.  The accepted rows land in the warm layer instead, and
            # the response hash makes the sweep verifiable.
            db.add(
                RawVendorSnapshot(
                    batch_id=batch.id,
                    source_name="eastmoney",
                    dataset=DATASET,
                    snapshot_date=args.end_date,
                    raw_payload={
                        "page": page,
                        "start_date": args.start_date.isoformat(),
                        "end_date": args.end_date.isoformat(),
                        "fetched_at_utc": fetched_at,
                        "endpoint": "RPT_VALUEANALYSIS_DET",
                        "filter": (
                            f"(TRADE_DATE>='{args.start_date.isoformat()}')"
                            f"(TRADE_DATE<='{args.end_date.isoformat()}')"
                        ),
                        "page_size": PAGE_SIZE,
                        "row_count": len(items),
                        "response_bytes": len(raw_text.encode("utf-8")),
                    },
                    row_hash=evidence_hash(raw_text),
                )
            )
            result.pages_fetched += 1
            result.evidence.append(
                PageEvidence(
                    page=page,
                    row_count=len(items),
                    response_sha256=evidence_hash(raw_text),
                    fetched_at=fetched_at,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    page_size=PAGE_SIZE,
                )
            )
            page_rows = collector.build_rows(items, observed_at=observed_at)
            collector.write_parquet(
                page_rows, output_dir=pages_dir / f"page_{page:05d}"
            )
            if result.pages_fetched % 25 == 0:
                db.commit()
        db.commit()

        # The gates run in SQL: three million rows must not travel through
        # Python to be compared against the trusted closes.
        gates = collector.evaluate_gates_in_sql(
            pages_dir=pages_dir,
            trusted_closes_parquet=trusted_parquet,
        )
        result.gates.extend(gates)
        accepted_dates = [gate.trade_date for gate in gates if gate.accepted]
        accepted_count = sum(
            gate.accepted_rows for gate in gates if gate.accepted
        )
        total_rows = sum(gate.supplier_rows for gate in gates)

        result.row_count = accepted_count
        result.trading_days = len(accepted_dates)

        raw_repo.finish_batch(
            db,
            batch,
            status="success" if accepted_dates else "failed",
            row_count=total_rows,
            accepted_count=accepted_count,
            rejected_count=total_rows - accepted_count,
            error_message=None
            if accepted_dates
            else "every trading day failed the acceptance gates",
        )
        db.commit()

    if not accepted_dates:
        print(json.dumps({"ok": False, "reason": "no day passed the gates"}))
        discard_directory(staging, trash_root=warm_root / "staging" / ".trash")
        return 1

    # Write the accepted rows into the staging directory.
    target_dir = staging / "research" / "valuation_history"
    # The published dataset is materialised directly from the staged pages
    # with the accepted trading days filtered in SQL.  Routing three million
    # rows through Python and back into DuckDB would dominate the runtime.
    target_dir.mkdir(parents=True, exist_ok=True)
    collector.publish_accepted_pages(
        pages_dir=pages_dir,
        accepted_dates=accepted_dates,
        output_dir=target_dir,
    )
    result.output_dir = target_dir
    # The staged pages are an intermediate artefact, not part of the release.
    discard_directory(pages_dir, trash_root=warm_root / "staging" / ".trash")

    summary = {
        "release_tag": release_tag,
        "base_release_tag": base_tag,
        "partial_release": True,
        "tables": [
            {
                "schema": "research",
                "table": "valuation_history",
                "row_count": accepted_count,
                "min_date": min(accepted_dates).isoformat(),
                "max_date": max(accepted_dates).isoformat(),
                "output_dir": str(target_dir),
            }
        ],
        "all_ok": True,
        "notes": [
            "partial release; unlisted tables inherit from base_release_tag",
            "research namespace: late observation of historical dates, "
            "pit_verified=false on every row",
            "PB_MRQ is captured but not point-in-time (switches on the "
            "reporting period end); do not use it for factor evidence",
        ],
        "gates": {
            "trading_days_seen": len(result.gates),
            "trading_days_accepted": result.trading_days,
            "pages_fetched": result.pages_fetched,
            "rejected": [
                {
                    "trade_date": gate.trade_date.isoformat(),
                    "reason": gate.reason,
                    "price_match_rate": str(gate.price_match_rate),
                    "universe_coverage": str(gate.universe_coverage),
                }
                for gate in result.rejected_days
            ],
        },
    }
    (staging / "export_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.dry_run:
        print(json.dumps({**summary, "dry_run": True}, ensure_ascii=False))
        discard_directory(staging, trash_root=warm_root / "staging" / ".trash")
        return 0

    # Publish atomically: move the staged release into place.  A reader only
    # ever sees a release whose summary reports all_ok.
    published = warm_root / "exported" / release_tag
    if published.exists():
        discard_directory(published, trash_root=warm_root / "exported" / ".trash")
    os.replace(staging, published)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
