from datetime import date
from types import SimpleNamespace

from scripts.maintenance import backfill_missing_kline


class _SessionContext:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, traceback):
        return False


def test_cli_prints_diagnostic_and_partial_status(monkeypatch, capsys) -> None:
    result = SimpleNamespace(
        batch_no=None,
        expected_rows=0,
        fetched_rows=0,
        inserted_rows=0,
        skipped_rows=0,
        status="partial",
        missing_rows=1,
        partial_rows=0,
        suspect_rows=1,
        status_explained_rows=0,
        lifecycle_unknown_rows=0,
        status_conflict_rows=0,
        unresolved_rows=1,
        unresolved_actionable_rows=0,
        skipped_unchanged_rows=0,
        skipped_duplicate_rows=0,
        ignored_in_scope_rows=0,
        rejected_rows=0,
        conflicting_response_rows=0,
        source_error_count=0,
    )
    monkeypatch.setattr(backfill_missing_kline, "SessionLocal", _SessionContext)
    monkeypatch.setattr(
        backfill_missing_kline,
        "MissingDailyBarBackfillService",
        lambda: SimpleNamespace(backfill=lambda db, **kwargs: result),
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "backfill_missing_kline.py",
            "--symbols",
            "600000.SH",
            "--start-date",
            date(2026, 1, 5).isoformat(),
            "--end-date",
            date(2026, 1, 5).isoformat(),
        ],
    )

    assert backfill_missing_kline.main() == 0
    output = capsys.readouterr().out
    assert "status=partial" in output
    assert "suspect_rows=1" in output
    assert "unresolved_rows=1" in output
