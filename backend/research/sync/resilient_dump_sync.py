"""Resilient HiThink dump downloader + ingest.

The vendor endpoint sits behind a proxy that frequently drops mid-stream, which
leaves a truncated ``.parquet`` that DuckDB cannot open. This helper downloads
with HTTP Range resume, verifies the parquet magic bytes and only then swaps the
file into the cache path the rest of the pipeline expects.

Usage::

    python backend/research/sync/resilient_dump_sync.py --kind recent
    python backend/research/sync/resilient_dump_sync.py --kind recent --ingest
"""

from __future__ import annotations

import argparse
import sys
import time
from json import loads
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from app.config.settings import settings  # noqa: E402
from app.data_sources.hithink_dump import (  # noqa: E402
    HithinkDumpDataSource,
    HithinkDumpKind,
)

PARQUET_MAGIC = b"PAR1"


def _open(url: str, *, headers: dict[str, str] | None = None, timeout: int = 120):
    return urlopen(Request(url, headers=headers or {}, method="GET"), timeout=timeout)


def presigned_url(kind: HithinkDumpKind, *, attempts: int = 8) -> str:
    url = f"{settings.hithink_finance_base_url.rstrip('/')}/api/dump/market-dumps/{kind.value}/download-url"
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with _open(url, headers={"X-api-key": settings.hithink_finance_api_key or ""}) as resp:
                envelope = loads(resp.read().decode("utf-8"))
            if envelope.get("code") != 0:
                raise RuntimeError(envelope.get("message") or "presign rejected")
            return (envelope.get("data") or {})["presigned_url"]
        except Exception as exc:  # noqa: BLE001 - retry any transport failure
            last = exc
            time.sleep(min(2 * (attempt + 1), 10))
    raise RuntimeError(f"presign failed after {attempts} attempts: {last}")


def _looks_complete(path: Path) -> bool:
    if not path.exists() or path.stat().st_size < 1024:
        return False
    with path.open("rb") as handle:
        head = handle.read(4)
        handle.seek(-4, 2)
        tail = handle.read(4)
    return head == PARQUET_MAGIC and tail == PARQUET_MAGIC


def download(kind: HithinkDumpKind, *, target: Path, max_rounds: int = 60) -> Path:
    """Download ``kind`` to ``target``, resuming across dropped connections."""
    part = target.with_suffix(".parquet.part")
    target.parent.mkdir(parents=True, exist_ok=True)

    for round_no in range(max_rounds):
        if _looks_complete(part):
            break
        url = presigned_url(kind)
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with _open(url, headers=headers) as resp:
                mode = "ab" if have and resp.status == 206 else "wb"
                if mode == "wb":
                    have = 0
                with part.open(mode) as out:
                    while chunk := resp.read(256 * 1024):
                        out.write(chunk)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            print(f"  round {round_no}: stalled at {have} bytes ({type(exc).__name__})", flush=True)
            time.sleep(min(2 + round_no, 8))
            continue
        size = part.stat().st_size
        if _looks_complete(part):
            print(f"  round {round_no}: complete, {size} bytes", flush=True)
            break
        print(f"  round {round_no}: partial {size} bytes, resuming", flush=True)
        time.sleep(1)
    else:
        raise RuntimeError(f"download did not converge: {part}")

    part.replace(target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["recent", "full", "adjustment"], default="recent")
    parser.add_argument("--ingest", action="store_true", help="collect into raw/market tables")
    parser.add_argument("--end-date", default=None, help="cap date for market build (YYYY-MM-DD)")
    args = parser.parse_args()

    kind = {
        "recent": HithinkDumpKind.RECENT_DAILY,
        "full": HithinkDumpKind.FULL_DAILY,
        "adjustment": HithinkDumpKind.ADJUSTMENT_FACTORS,
    }[args.kind]
    target = Path(settings.hithink_dump_cache_dir) / f"{kind.value}.parquet"
    print(f"downloading {kind.value} -> {target}", flush=True)
    path = download(kind, target=target)

    source = HithinkDumpDataSource()
    print("date range:", source.daily_date_range(path), flush=True)

    if args.ingest:
        from datetime import date

        from app.data_sources.hithink_dump import HithinkDump
        from app.database.session import SessionLocal
        from app.services.hithink_dump_collection import HithinkDumpCollectionService

        cap = date.fromisoformat(args.end_date) if args.end_date else None
        service = HithinkDumpCollectionService(data_source=source)
        dump = HithinkDump(kind=kind, path=path, release_tag=path.name)
        with SessionLocal() as db:
            result = service.collect(db, full=kind == HithinkDumpKind.FULL_DAILY, dump=dump)
            print(
                f"collected batch={result.batch_no} rows={result.row_count} "
                f"accepted={result.accepted_count} skipped={result.skipped_count}",
                flush=True,
            )
            market_rows = service.build_market_daily(db, batch_no=result.batch_no, end_date=cap)
            print("market rows built:", market_rows, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
