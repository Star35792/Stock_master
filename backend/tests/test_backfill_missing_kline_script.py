from datetime import date
from types import SimpleNamespace

import pytest

from scripts.maintenance import backfill_missing_kline


class _SessionContext:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, traceback):
        return False


def _result(status: str) -> SimpleNamespace:
    return SimpleNamespace(
        batch_no=None,
        expected_rows=0,
        fetched_rows=0,
        inserted_rows=0,
        skipped_rows=0,
        status=status,
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


def _args(*, strict: bool = False) -> list[str]:
    args = [
        "--symbols",
        "600000.SH",
        "--start-date",
        date(2026, 1, 5).isoformat(),
        "--end-date",
        date(2026, 1, 5).isoformat(),
    ]
    return [*args, "--strict"] if strict else args


def _stub_service(monkeypatch, *, status: str) -> None:
    result = _result(status)
    monkeypatch.setattr(backfill_missing_kline, "SessionLocal", _SessionContext)
    monkeypatch.setattr(
        backfill_missing_kline,
        "MissingDailyBarBackfillService",
        lambda: SimpleNamespace(backfill=lambda db, **kwargs: result),
    )


def test_cli_prints_diagnostic_and_partial_status(monkeypatch, capsys) -> None:
    _stub_service(monkeypatch, status="partial")

    assert backfill_missing_kline.main(_args()) == 0
    output = capsys.readouterr().out
    assert "status=partial" in output
    assert "suspect_rows=1" in output
    assert "unresolved_rows=1" in output


@pytest.mark.parametrize(
    ("status", "expected_exit_code"),
    [("success", 0), ("partial", 1), ("failed", 1)],
)
def test_cli_strict_exit_code_follows_final_status(
    monkeypatch, status: str, expected_exit_code: int
) -> None:
    _stub_service(monkeypatch, status=status)

    assert backfill_missing_kline.main(_args(strict=True)) == expected_exit_code
