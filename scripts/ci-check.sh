#!/usr/bin/env bash
# Compatibility entry point; commands live in ci_check.py.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

exec uv run --project "$REPO_ROOT/backend" --extra dev python "$REPO_ROOT/scripts/ci_check.py" "$@"
