"""Shared local/CI checks. Run from any directory with Python 3.13."""

import argparse
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def check_steps(scope: str) -> list[tuple[Path, list[str]]]:
    steps = []
    if scope in {"all", "backend"}:
        steps.extend(
            [
                (
                    REPO_ROOT / "backend",
                    [
                        "uv",
                        "run",
                        "--extra",
                        "dev",
                        "ruff",
                        "check",
                        "app",
                        "tests",
                        "../scripts/ci_check.py",
                    ],
                ),
                (REPO_ROOT / "backend", ["uv", "run", "--extra", "dev", "pytest", "-q"]),
            ]
        )
    if scope in {"all", "frontend"}:
        steps.extend(
            [
                (REPO_ROOT / "frontend", ["npm", "run", "--if-present", "test"]),
                (REPO_ROOT / "frontend", ["npm", "run", "build"]),
            ]
        )
    return steps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("all", "backend", "frontend"), default="all")
    parser.add_argument("--list", action="store_true", help="Print commands without running them")
    args = parser.parse_args(argv)
    for directory, command in check_steps(args.scope):
        print(f"==> {directory.name}: {' '.join(command)}", flush=True)
        if args.list:
            continue
        executable = shutil.which(command[0])
        if executable is None:
            print(f"Missing executable on PATH: {command[0]}", flush=True)
            return 127
        try:
            subprocess.run([executable, *command[1:]], cwd=directory, check=True)
        except subprocess.CalledProcessError as error:
            return error.returncode
        except OSError as error:
            print(f"Unable to run {command[0]}: {error}", flush=True)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
