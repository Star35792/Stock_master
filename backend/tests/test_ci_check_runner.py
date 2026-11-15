"""The shared check runner must preserve coverage and stop on failures."""

import runpy
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[2] / "scripts" / "ci_check.py"


@pytest.fixture
def runner():
    return runpy.run_path(str(RUNNER))


def test_backend_checks_run_the_full_suite_once(runner):
    steps = runner["check_steps"]("backend")
    assert [command for _, command in steps] == [
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
        # The pytest step pins its temporary directory inside the repo: the
        # host environment's bulk-delete guard aborts the process once its
        # unlink budget is exhausted, which turns unrelated tests into
        # failures.  A repo-local basetemp keeps the run reproducible.
        [
            "uv",
            "run",
            "--extra",
            "dev",
            "pytest",
            "-q",
            f"--basetemp={RUNNER.parents[1] / 'backend' / '.pytest_tmp' / 'ci'}",
        ],
    ]
    assert all(directory == RUNNER.parents[1] / "backend" for directory, _ in steps)


def test_frontend_checks_keep_workflow_tests_and_build(runner):
    steps = runner["check_steps"]("frontend")
    assert [command for _, command in steps] == [
        ["bun", "test"],
        ["bun", "run", "build"],
    ]
    assert all(directory == RUNNER.parents[1] / "frontend" for directory, _ in steps)
    assert runner["check_steps"]("all") == (runner["check_steps"]("backend") + steps)


def test_list_does_not_require_installed_tools(runner, monkeypatch):
    def unexpected_lookup(*args):
        pytest.fail("listing commands must not look up or run executables")

    monkeypatch.setattr(runner["shutil"], "which", unexpected_lookup)
    assert runner["main"](["--list"]) == 0


def test_failure_stops_remaining_checks_and_preserves_exit_code(runner, monkeypatch):
    calls = []
    monkeypatch.setattr(runner["shutil"], "which", lambda name: f"/tools/{name}")

    def fail(command, *, cwd, check):
        calls.append((command, cwd, check))
        raise subprocess.CalledProcessError(7, command)

    monkeypatch.setattr(runner["subprocess"], "run", fail)
    assert runner["main"]([]) == 7
    assert len(calls) == 1
    assert calls[0][0][0] == "/tools/uv"
    assert calls[0][1] == RUNNER.parents[1] / "backend"
    assert calls[0][2] is True


def test_missing_executable_fails_clearly(runner, monkeypatch, capsys):
    monkeypatch.setattr(runner["shutil"], "which", lambda name: None)
    assert runner["main"](["--scope", "frontend"]) == 127
    assert "Missing executable on PATH: bun" in capsys.readouterr().out


def test_frontend_checks_require_bun_without_package_manager_fallback(
    runner, monkeypatch, capsys
):
    """The frontend has a single runtime: bun never falls back to npm or pnpm."""
    calls = []

    def find_executable(name):
        return {"bun": None, "npm": r"C:\runtime\npm.cmd", "pnpm": r"C:\runtime\pnpm.cmd"}.get(
            name
        )

    def succeed(command, *, cwd, check):
        calls.append((command, cwd, check))

    monkeypatch.setattr(runner["shutil"], "which", find_executable)
    monkeypatch.setattr(runner["subprocess"], "run", succeed)

    assert runner["main"](["--scope", "frontend"]) == 127
    assert calls == []
    assert "Missing executable on PATH: bun" in capsys.readouterr().out
