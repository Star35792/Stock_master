import hashlib
import json
import re
import subprocess
from datetime import date, datetime
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_ROOT = REPO_ROOT / "deliverables" / "research" / "evidence" / "value-investing"
MANIFEST_RELATIVE_PATH = PurePosixPath(
    "deliverables/research/evidence/value-investing/manifest.json"
)
MANIFEST_PATH = REPO_ROOT.joinpath(*MANIFEST_RELATIVE_PATH.parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=REPO_ROOT)


def _repo_path(raw_path: str, *, parent: Path) -> Path:
    assert "\\" not in raw_path
    relative_path = PurePosixPath(raw_path)
    assert not relative_path.is_absolute()
    assert raw_path == relative_path.as_posix()
    assert "." not in relative_path.parts
    assert ".." not in relative_path.parts
    resolved = REPO_ROOT.joinpath(*relative_path.parts).resolve()
    assert resolved.is_relative_to(parent.resolve())
    return resolved


def test_value_investing_evidence_manifest_binds_single_tracked_copies() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 1
    assert manifest["artifact_class"] == "research_evidence"
    assert manifest["authority"] == "non_authoritative"
    assert manifest["temporal_status"] == "frozen_historical"
    assert manifest["evidence_level"] == "research_only"
    assert manifest["reference_trade_date"] == "2026-11-10"
    assert manifest["point_in_time_verified"] is False
    assert manifest["ex_post_research"] is True
    assert manifest["allowed_consumers"] == [
        "human_research_review",
        "research_card_reproduction",
        "migration_integrity_validation",
    ]
    assert manifest["prohibited_uses"] == [
        "factor_gate",
        "current_shortlist",
        "execution",
        "production_runtime_input",
    ]
    assert manifest["constraints"] == {
        "factor_gate_eligible": False,
        "execution_eligible": False,
        "authoritative_runtime_source": False,
        "retention": "single tracked copy; runtime outputs remain ignored",
    }
    artifacts = manifest["artifacts"]
    assert {artifact["role"] for artifact in artifacts} == {
        "external_research_observations",
        "frozen_value_screen_reference",
    }

    for artifact in artifacts:
        path = _repo_path(artifact["path"], parent=EVIDENCE_ROOT)
        origin_path = _repo_path(
            artifact["origin_path"], parent=REPO_ROOT / "backend" / "outputs"
        )
        assert path.is_file()
        assert path.stat().st_size == artifact["bytes"]
        assert _sha256(path) == artifact["sha256"]
        assert not origin_path.exists()

        origin_commit = artifact["origin_commit"]
        assert re.fullmatch(r"[0-9a-f]{40}", origin_commit)
        origin_blob_oid = _git(
            "rev-parse", f"{origin_commit}:{artifact['origin_path']}"
        ).decode().strip()
        current_blob_oid = _git(
            "hash-object", f"--path={artifact['path']}", artifact["path"]
        ).decode().strip()
        assert current_blob_oid == origin_blob_oid
        assert _git("check-attr", "eol", "--", artifact["path"]).decode().strip().endswith(
            "eol: crlf"
        )

        same_size_paths = []
        for raw_tracked_path in _git("ls-files", "-z").decode().split("\0"):
            if not raw_tracked_path:
                continue
            tracked_path = REPO_ROOT.joinpath(*PurePosixPath(raw_tracked_path).parts)
            if tracked_path.is_file() and tracked_path.stat().st_size == artifact["bytes"]:
                if _sha256(tracked_path) == artifact["sha256"]:
                    same_size_paths.append(raw_tracked_path)
        assert same_size_paths == [artifact["path"]]

        old_path_references = set(
            _git("grep", "-l", "-F", "--", artifact["origin_path"])
            .decode()
            .splitlines()
        )
        assert old_path_references == {MANIFEST_RELATIVE_PATH.as_posix()}

    frozen_screen = next(
        artifact
        for artifact in artifacts
        if artifact["role"] == "frozen_value_screen_reference"
    )
    reference_date = date.fromisoformat(manifest["reference_trade_date"])
    assert date.fromisoformat(frozen_screen["target_trade_date"]) == reference_date
    assert datetime.fromisoformat(frozen_screen["generated_at"]).date() > reference_date

    production_references = subprocess.run(
        [
            "git",
            "grep",
            "-n",
            "-F",
            "--",
            "deliverables/research/evidence",
            "--",
            "backend/app/**/*.py",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert production_references.returncode == 1, production_references.stdout
