import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cleanup_test_artifacts.py"
spec = importlib.util.spec_from_file_location("cleanup_test_artifacts", SCRIPT)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


def test_cleanup_preview_and_exact_target_preserve_business_data(tmp_path):
    run = tmp_path / "backend" / ".pytest_tmp" / "ci"
    run.mkdir(parents=True)
    (run / "fixture.duckdb").write_bytes(b"test fixture")
    data = tmp_path / "backend" / "data"
    data.mkdir()
    (data / "production.duckdb").write_bytes(b"business fact")
    plan = cleanup.cleanup_run("ci", repo_root=tmp_path)
    assert plan["files"] == 1 and plan["removed"] is False
    result = cleanup.cleanup_run(
        "ci",
        apply=True,
        repo_root=tmp_path,
        expected_manifest=plan["manifest"],
    )
    assert result["removed"] is True
    assert not run.exists()
    assert (data / "production.duckdb").read_bytes() == b"business fact"


@pytest.mark.parametrize("name", ["..", "../data", "D:/data", "/data", "ci/subdir", ""])
def test_cleanup_rejects_paths(name, tmp_path):
    with pytest.raises(ValueError):
        cleanup.inspect_run(name, repo_root=tmp_path)


def test_cleanup_rejects_changed_preview(tmp_path):
    run = tmp_path / "backend" / ".pytest_tmp" / "ci"
    run.mkdir(parents=True)
    artifact = run / "fixture"
    artifact.write_text("first")
    plan = cleanup.inspect_run("ci", repo_root=tmp_path)
    artifact.write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        cleanup.cleanup_run(
            "ci",
            apply=True,
            repo_root=tmp_path,
            expected_manifest=plan["manifest"],
        )
    assert artifact.exists()


def test_cleanup_rejects_links(tmp_path, monkeypatch):
    run = tmp_path / "backend" / ".pytest_tmp" / "ci"
    run.mkdir(parents=True)
    target = run / "link"
    target.write_text("stand-in for reparse point")
    original = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda self: self == target or original(self))
    with pytest.raises(ValueError, match="reparse"):
        cleanup.cleanup_run("ci", apply=True, repo_root=tmp_path)
    assert target.exists()
