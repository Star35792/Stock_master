#!/usr/bin/env bash
# Local CI check script for Stock Master.
# Run from the repository root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Backend ruff"
cd "$REPO_ROOT/backend"
uv run --extra dev ruff check app tests

echo "==> Backend core tests"
uv run --extra dev pytest tests/test_api.py tests/test_backtest_engine.py tests/test_backtest_fees.py tests/test_backtest_service.py tests/test_portfolio_builder.py tests/test_paper_execution.py tests/test_risk_service.py tests/test_strategy_templates.py -q

echo "==> Backend full test suite"
uv run --extra dev pytest -q

echo "==> Backend alembic offline check (non-blocking)"
uv run --extra dev alembic check || true

echo "==> Frontend type-check + build"
cd "$REPO_ROOT/frontend"
npm run build

echo "==> All checks passed"
