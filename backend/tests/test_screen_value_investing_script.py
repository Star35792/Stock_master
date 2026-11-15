import json
import os
from datetime import date
from pathlib import Path

import pytest

from scripts.research import screen_value_investing as script


def _temporary_files(root: Path) -> list[Path]:
    return list(root.glob(".*.tmp"))


def test_publish_report_files_replaces_complete_pair_and_cleans_temps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    json_path = tmp_path / "screen.json"
    markdown_path = tmp_path / "screen.md"
    json_path.write_text("old-json", encoding="utf-8")
    markdown_path.write_text("old-markdown", encoding="utf-8")
    synced: list[int] = []
    monkeypatch.setattr(script.os, "fsync", synced.append)

    script._publish_report_files(
        json_path,
        markdown_path,
        json_text='{"status":"new"}',
        markdown_text="# new report",
    )

    assert json_path.read_text(encoding="utf-8") == '{"status":"new"}'
    assert markdown_path.read_text(encoding="utf-8") == "# new report"
    assert len(synced) >= 2
    assert _temporary_files(tmp_path) == []


def test_publish_report_files_leaves_old_pair_when_staging_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    json_path = tmp_path / "screen.json"
    markdown_path = tmp_path / "screen.md"
    json_path.write_text("old-json", encoding="utf-8")
    markdown_path.write_text("old-markdown", encoding="utf-8")
    real_fsync = os.fsync
    calls = 0

    def fail_second_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated sync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(script.os, "fsync", fail_second_fsync)

    with pytest.raises(OSError, match="simulated sync failure"):
        script._publish_report_files(
            json_path,
            markdown_path,
            json_text="new-json",
            markdown_text="new-markdown",
        )

    assert json_path.read_text(encoding="utf-8") == "old-json"
    assert markdown_path.read_text(encoding="utf-8") == "old-markdown"
    assert _temporary_files(tmp_path) == []


def test_publish_report_files_rolls_back_markdown_when_json_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    json_path = tmp_path / "screen.json"
    markdown_path = tmp_path / "screen.md"
    json_path.write_text("old-json", encoding="utf-8")
    markdown_path.write_text("old-markdown", encoding="utf-8")
    real_replace = os.replace

    def fail_json_replace(source: str | Path, target: str | Path) -> None:
        if Path(target) == json_path:
            raise OSError("simulated replace failure")
        real_replace(source, target)

    monkeypatch.setattr(script.os, "replace", fail_json_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        script._publish_report_files(
            json_path,
            markdown_path,
            json_text="new-json",
            markdown_text="new-markdown",
        )

    assert json_path.read_text(encoding="utf-8") == "old-json"
    assert markdown_path.read_text(encoding="utf-8") == "old-markdown"
    assert _temporary_files(tmp_path) == []


def test_publish_report_files_removes_new_markdown_when_first_json_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    json_path = tmp_path / "screen.json"
    markdown_path = tmp_path / "screen.md"
    real_replace = os.replace

    def fail_json_replace(source: str | Path, target: str | Path) -> None:
        if Path(target) == json_path:
            raise OSError("simulated first publish failure")
        real_replace(source, target)

    monkeypatch.setattr(script.os, "replace", fail_json_replace)

    with pytest.raises(OSError, match="simulated first publish failure"):
        script._publish_report_files(
            json_path,
            markdown_path,
            json_text="new-json",
            markdown_text="new-markdown",
        )

    assert not json_path.exists()
    assert not markdown_path.exists()
    assert _temporary_files(tmp_path) == []


def test_report_paths_reject_markdown_as_the_json_target(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not be a .md path"):
        script._report_paths(tmp_path / "screen.md")


def test_main_publishes_matching_report_files_and_prints_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = {
        "strategy": "value-investing",
        "strategy_version": "test-v1",
        "required_margin": "0.30",
        "target_trade_date": "2026-11-10",
        "universe_count": 1,
        "qualified_count": 0,
        "shortlist_count": 0,
        "qualified_symbols": [],
        "snapshot_hash": "snapshot-test",
        "params": {},
        "price_gate_only": {},
    }

    class StubService:
        def run(self, *, as_of: date | None) -> dict:
            assert as_of == date(2026, 11, 10)
            return report

    monkeypatch.setattr(script, "_ensure_registered", lambda: None)
    monkeypatch.setattr(script, "ValueInvestingScreenService", StubService)
    monkeypatch.setattr(script, "_summary_markdown", lambda value: "# matching summary")
    output = tmp_path / "screen.json"

    assert (
        script.main(["--as-of", "2026-11-10", "--output", str(output)]) == 0
    )

    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert output.with_suffix(".md").read_text(encoding="utf-8") == "# matching summary"
    summary = json.loads(capsys.readouterr().out)
    assert summary["output"] == str(output)
    assert summary["markdown"] == str(output.with_suffix(".md"))
    assert _temporary_files(tmp_path) == []
