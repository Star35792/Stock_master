from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    ROOT
    / "deliverables"
    / "research"
    / "backtests"
    / "20261020-low-price-rebound-intent-manifest.json"
)


def test_low_price_rebound_manifest_locks_the_full_artifact_set() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["evidence_level"] == "restricted_research_only"
    assert {artifact["role"] for artifact in manifest["artifacts"]} == {
        "daily_equity_curve",
        "rebalance_picks",
        "simulated_trades",
        "metrics_and_data_snapshot",
    }

    for artifact in manifest["artifacts"]:
        path = ROOT / artifact["path"]
        assert path.is_file(), artifact["path"]
        assert path.stat().st_size == artifact["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]

    summary = next(
        artifact
        for artifact in manifest["artifacts"]
        if artifact["role"] == "metrics_and_data_snapshot"
    )
    payload = json.loads((ROOT / summary["path"]).read_text(encoding="utf-8"))
    assert payload["params"]["rank_mode"] == manifest["run"]["rank_mode"]
    for name, value in manifest["reported_metrics"].items():
        assert payload["metrics"][name] == value
