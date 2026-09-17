---
name: verify
summary: Verify backend runtime surfaces after product-code changes.
---

# Backend Verification

Run from `backend/`.

## HiThink dump ingestion

1. Drive the real CLI with the recent dump:
   ```bash
   uv run python scripts/ingest/hithink/sync_hithink_dump.py --skip-reference-data
   ```
2. For full-history changes, drive the 10-year path:
   ```bash
   uv run python scripts/ingest/hithink/sync_hithink_dump.py --full --skip-reference-data
   ```
3. Start the API on an unused port and request `/health` plus a known symbol's earliest bars. Stop only the PID created by that shell:
   ```bash
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8011 & server_pid=$!
   curl -s http://127.0.0.1:8011/health
   curl -s 'http://127.0.0.1:8011/api/v1/daily-bars/600519.SH?limit=2&offset=0'
   kill $server_pid
   ```

## Useful probes

- Repeat the recent dump command to verify idempotent market upserts.
- Pass an invalid CLI option and expect argparse exit code 2 with a clear message.
- The CLI reports `market_rows=0` under DuckDB because SQLAlchemy does not provide a reliable affected-row count for `INSERT ... ON CONFLICT`; verify persisted market rows through the API instead.
