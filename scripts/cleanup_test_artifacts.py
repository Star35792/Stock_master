"""Preview or manually remove one repository-local pytest run directory.

Run only after pytest exits. This tool never accepts an arbitrary filesystem
path and never visits business data, WAL, warm releases, or source directories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_FILES = 200_000
MAX_BYTES = 5 * 1024**3


def _reject_link(path: Path) -> None:
    info = path.lstat()
    if (
        path.is_symlink()
        or path.is_junction()
        or (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    ):
        raise ValueError(f"Refusing link or reparse point: {path}")


def inspect_run(run_dir: str, *, repo_root: Path = REPO_ROOT) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", run_dir):
        raise ValueError("run-dir must be one plain directory name")
    if run_dir in {".", ".."}:
        raise ValueError("invalid run directory")
    root = repo_root.resolve(strict=True)
    parent = root / "backend" / ".pytest_tmp"
    target = parent / run_dir
    for path in (root / "backend", parent, target):
        if path.exists() or path.is_symlink() or path.is_junction():
            _reject_link(path)
    if not target.exists():
        return {"path": str(target), "exists": False, "files": 0, "bytes": 0}
    if not target.is_dir() or target.resolve().parent != parent.resolve():
        raise ValueError("cleanup target must be a direct pytest run directory")
    entries = []
    total_bytes = 0
    for current, directories, filenames in os.walk(target, followlinks=False):
        for name in directories + filenames:
            item = Path(current) / name
            _reject_link(item)
            info = item.stat()
            relative = item.relative_to(target).as_posix()
            if name in filenames:
                total_bytes += info.st_size
            entries.append((relative, info.st_size, info.st_mtime_ns, item.is_dir()))
            if len(entries) > MAX_FILES or total_bytes > MAX_BYTES:
                raise ValueError("test artifact cleanup exceeds the bounded file/size limit")
    digest = hashlib.sha256(json.dumps(sorted(entries)).encode()).hexdigest()
    return {
        "path": str(target),
        "exists": True,
        "files": sum(not entry[3] for entry in entries),
        "bytes": total_bytes,
        "manifest": digest,
    }


def cleanup_run(
    run_dir: str,
    *,
    apply: bool = False,
    repo_root: Path = REPO_ROOT,
    expected_manifest: str | None = None,
) -> dict:
    plan = inspect_run(run_dir, repo_root=repo_root)
    if expected_manifest is not None and plan.get("manifest") != expected_manifest:
        raise ValueError("test directory changed since the reviewed preview")
    if not apply or not plan["exists"]:
        return {**plan, "removed": False}
    if inspect_run(run_dir, repo_root=repo_root) != plan:
        raise ValueError("test directory changed during inspection; stop pytest first")
    shutil.rmtree(plan["path"])
    return {**plan, "removed": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        nargs="+",
        default=["ci"],
        help="explicit children of backend/.pytest_tmp; no wildcards",
    )
    parser.add_argument("--apply", action="store_true", help="delete after pytest has exited")
    parser.add_argument("--expected-manifest", help="optional SHA-256 from the preview")
    args = parser.parse_args(argv)
    try:
        if args.expected_manifest and len(args.run_dir) != 1:
            raise ValueError("expected-manifest requires exactly one run directory")
        result = [
            cleanup_run(
                run_dir,
                apply=args.apply,
                expected_manifest=args.expected_manifest,
            )
            for run_dir in args.run_dir
        ]
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Cleanup stopped: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
