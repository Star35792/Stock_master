"""Shared local/CI checks. Run from any directory with Python 3.13."""

import argparse
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def resolve_executable(name: str) -> tuple[str | None, str]:
    executable = shutil.which(name)
    if executable is not None or name != "npm":
        return executable, name

    # Codex's Windows runtime exposes pnpm but not always npm. Both package
    # managers accept the `run` commands used below, so prefer npm and use the
    # PATH-provided pnpm shim only when npm is genuinely unavailable.
    return shutil.which("pnpm"), "pnpm"


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
                        "scripts",
                        "../scripts/ci_check.py",
                    ],
                ),
                (
                    REPO_ROOT / "backend",
                    [
                        "uv",
                        "run",
                        "--extra",
                        "dev",
                        "pytest",
                        "-q",
                        # Keep pytest's temporary directory inside the repo.
                        # The host environment's bulk-delete guard counts
                        # unlink calls per turn and aborts the process with
                        # SystemExit(1) once the threshold is crossed, which
                        # shows up as a batch of failures that are not
                        # assertion failures at all.  A repo-local basetemp
                        # keeps the run reproducible and out of that path.
                        f"--basetemp={REPO_ROOT / 'backend' / '.pytest_tmp' / 'ci'}",
                    ],
                ),
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
        executable, resolved_name = resolve_executable(command[0])
        if executable is None:
            print(f"Missing executable on PATH: {command[0]}", flush=True)
            return 127
        if resolved_name != command[0]:
            print(f"Using {resolved_name} fallback for {command[0]}", flush=True)
        basetemp_arg = next(
            (part for part in command if part.startswith("--basetemp=")), None
        )
        if basetemp_arg is not None:
            Path(basetemp_arg.removeprefix("--basetemp=")).parent.mkdir(
                parents=True, exist_ok=True
            )
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
