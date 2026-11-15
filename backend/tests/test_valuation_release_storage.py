import json
import sys

import pytest

from scripts.ingest.eastmoney import collect_valuation_history


def test_valuation_sweep_refuses_existing_release_before_staging(tmp_path, monkeypatch) -> None:
    warm_root = tmp_path / "warm"
    parent = warm_root / "exported" / "20261111-100000"
    published = warm_root / "exported" / "20261111-110000"
    parent.mkdir(parents=True)
    published.mkdir(parents=True)
    (parent / "export_summary.json").write_text('{"all_ok": true}', encoding="utf-8")
    (published / "export_summary.json").write_text(
        json.dumps({"all_ok": True, "base_release_tag": parent.name}), encoding="utf-8"
    )
    before = (published / "export_summary.json").read_bytes()
    monkeypatch.setattr(collect_valuation_history, "initialize_database", lambda: None)
    monkeypatch.setattr(sys, "argv", [
        "collect_valuation_history",
        "--start-date", "2026-11-10",
        "--end-date", "2026-11-10",
        "--warm-root", str(warm_root),
        "--base-release-tag", parent.name,
        "--release-tag", published.name,
    ])

    with pytest.raises(SystemExit, match="release already exists"):
        collect_valuation_history.main()
    assert (published / "export_summary.json").read_bytes() == before
    assert not (warm_root / "staging" / published.name).exists()
