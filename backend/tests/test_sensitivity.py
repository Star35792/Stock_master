from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.routes import backtests
from app.main import app
from app.schemas.sensitivity import SensitivityRequest
from app.services.sensitivity import SensitivityService, axis_value
from app.strategies import MinimumScorePolicy, WeightingMode
from app.utils.duckdb_write_lock import DuckDbWriteLockTimeout

D = Decimal


def run(top_n=10, total="0.10", start="2026-01-01", end="2026-01-31"):
    parameters = dict(
        strategy_name="factor_selection",
        factor_specs=[{"factor_name": "momentum", "factor_version": "v1", "weight": "1"}],
        top_n=top_n,
        allowed_security_types=["stock"],
        coverage_ratio="1",
        weighting_mode=WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED.value,
        minimum_score_policy=MinimumScorePolicy.ALWAYS_FULLY_INVESTED.value,
        minimum_score=None,
        invested_weight="0.9",
        rebalance_rule="every_n_trading_days",
        rebalance_interval=5,
        portfolio_constraints=None,
        signal_lag=1,
        holding_period=1,
        start_date=start,
        end_date=end,
        test_start_date=start,
        test_end_date=end,
        training_start_date=None,
        training_end_date=None,
        validation_window_id=None,
        adjust_type="none",
        initial_cash="1000",
        commission_rate="0.001",
        slippage_bps="5",
        lot_size=100,
        fee_model_version="linear_commission_v1",
        corporate_action_policy="reject",
        engine_version="v1",
        calculation_precision_policy="decimal",
        validation_mode="none",
        code_version="source-a",
        data_version=f"different-overall-{top_n}",
        data_snapshot=dict(
            snapshot_version="trusted_market_slice_v1",
            verification_status="verified",
            quality_status="trusted",
            sha256="a" * 64,
            post_run_sha256="a" * 64,
        ),
        strategy_input_snapshot=dict(
            contract_version="strategy_factor_frame_v1", sha256="b" * 64, row_count=100
        ),
    )
    return dict(
        parameters=parameters,
        status="success",
        strategy_name="factor_selection",
        code_version="source-a",
        start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end),
        initial_cash=D(1000),
        run_total_return=D(total),
        total_return=D(total),
        maximum_drawdown=D("-0.05"),
        sharpe_ratio=D(0),
        performance_code_version="performance-v1",
    )


def evaluate(runs, axes=None, errors=None):
    ids = sorted(set(runs) | set(errors or {}))
    payload = SensitivityRequest(run_ids=ids, baseline_run_id=1, axes=axes or ["top_n"])
    return SensitivityService(lambda _: (deepcopy(runs), dict(errors or {}))).evaluate(payload)


def test_controlled_axis_retains_zero_ignores_overall_hash_and_never_mutates_runs():
    runs = {1: run(), 2: run(20, "0")}
    original = deepcopy(runs)
    report = evaluate(runs)
    assert [cell.status for cell in report.cells] == ["controlled", "controlled"]
    assert report.cells[1].delta_return == D("-0.10")
    assert report.summaries[1].worst_return == 0
    assert report.summaries[1].complete and not report.ranked
    assert report.contract_sha256 == evaluate(runs).contract_sha256
    assert runs == original


@pytest.mark.parametrize("change", ["fee", "pool", "factor", "market", "code", "old", "selection"])
def test_non_axis_changes_or_missing_identity_are_exploratory(change):
    candidate = run(20)
    p = candidate["parameters"]
    if change == "fee":
        p["commission_rate"] = "0.002"
    elif change == "pool":
        p["allowed_security_types"] = ["etf"]
    elif change in ("factor", "market"):
        p["strategy_input_snapshot" if change == "factor" else "data_snapshot"]["sha256"] = "c" * 64
    elif change == "code":
        candidate["code_version"] = p["code_version"] = "source-b"
    elif change == "old":
        p.pop("lot_size")
    else:
        p["selection_policy"] = {"name": "rolling"}
    cell = evaluate({1: run(), 2: candidate}).cells[1]
    assert cell.status == "exploratory" and cell.reasons and cell.delta_return is None


@pytest.mark.parametrize("bad", [None, D("NaN"), D("Infinity"), 0.1, D("0.20")])
def test_bad_baseline_never_controls_candidate_or_computes_delta(bad):
    baseline = run()
    baseline["total_return"] = bad
    report = evaluate({1: baseline, 2: run(20, "0.30")})
    assert report.cells[0].status == "unavailable"
    assert report.cells[1].status == "exploratory"
    assert report.cells[1].delta_return is None
    assert all(not summary.complete for summary in report.summaries)
    report.model_dump_json()  # no nonfinite values can escape into reported metrics


def test_two_axis_missing_combinations_failed_duplicate_and_missing_fold_are_visible():
    runs = {1: run(), 2: run(20), 3: run(), 4: run(20, start="2026-02-01", end="2026-02-28")}
    runs[2]["parameters"]["rebalance_interval"] = 10
    runs[3]["status"] = "failed"
    report = evaluate(runs, ["top_n", "rebalance_interval"])
    assert len(report.candidates) == 4 and len(report.cells) == 8
    assert {cell.status for cell in report.cells} >= {"duplicate", "missing", "exploratory"}
    assert all(
        not summary.complete and summary.median_return is None for summary in report.summaries
    )
    failed = evaluate({1: run(), 2: {**run(20), "status": "failed"}})
    assert failed.cells[1].status == "unavailable" and failed.cells[1].run_ids == [2]


def test_cross_window_versions_or_fixed_contract_cannot_drift_together():
    runs = {
        1: run(),
        2: run(20),
        3: run(start="2026-02-01", end="2026-02-28"),
        4: run(20, start="2026-02-01", end="2026-02-28"),
    }
    assert all(summary.complete for summary in evaluate(runs).summaries)
    for key in (3, 4):
        runs[key]["parameters"].update(
            training_start_date="2025-12-01",
            training_end_date="2025-12-31",
            validation_window_id="second-fold",
        )
    assert all(summary.complete for summary in evaluate(runs).summaries)
    for key in (3, 4):
        runs[key]["parameters"]["commission_rate"] = "0.002"
    report = evaluate(runs)
    assert all(cell.status == "exploratory" for cell in report.cells if cell.fold == "F2")


def test_unknown_and_unclassified_runs_block_completeness_and_remain_in_export():
    candidate = run(20)
    candidate["parameters"].pop("top_n")
    report = evaluate({1: run(), 2: candidate}, errors={3: "运行不存在。"})
    assert report.unclassified_run_ids == [2, 3]
    assert all(not item.complete for item in report.summaries)


@pytest.mark.parametrize("bad", ["1e-999999999", "1e999999999", "NaN", True, 0.1])
def test_axis_values_are_bounded_before_decimal_formatting(bad):
    with pytest.raises(ValueError):
        axis_value({"top_n": bad}, "top_n")


def test_grid_budget_checked_before_materializing_large_matrix():
    runs = {i: run(i, start=f"2026-01-{i:02}", end="2026-02-28") for i in range(1, 18)}
    with pytest.raises(ValueError, match="256"):
        evaluate(runs)


@pytest.mark.parametrize(
    "axis,bad",
    [
        ("top_n", None),
        ("top_n", "0.5"),
        ("top_n", 0),
        ("rebalance_interval", 251),
        ("invested_weight", "0"),
        ("coverage_ratio", "0"),
        ("minimum_score", "-0.1"),
        ("commission_rate", "-0.1"),
    ],
)
def test_invalid_axis_contract_is_unclassified(axis, bad):
    candidate = run(20)
    candidate["parameters"][axis] = bad
    report = evaluate({1: run(), 2: candidate}, [axis])
    assert report.unclassified_run_ids == [2]


def test_invalid_fixed_contract_cannot_pass_only_because_keys_exist():
    runs = {1: run(), 2: run(20)}
    for value in runs.values():
        value["parameters"]["signal_lag"] = None
    assert all(cell.status == "exploratory" for cell in evaluate(runs).cells)


def test_repository_projects_only_bounded_contract_and_saved_metrics(monkeypatch):
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import Session

    from app.database.research_base import ResearchBase
    from app.models.strategy import BacktestRun
    from app.repositories.strategy import sensitivity as repository

    engine = create_engine("duckdb:///:memory:")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE SCHEMA strategy"))
        ResearchBase.metadata.create_all(engine)
        with Session(engine) as db:
            for key, params in [(1, {"top_n": 10}), (2, {"large": "x" * 200})]:
                db.add(
                    BacktestRun(
                        id=key,
                        name=f"run-{key}",
                        strategy_name="factor_selection",
                        parameters=params,
                        code_version="v1",
                        data_version="v1",
                        start_date=date(2026, 1, 1),
                        end_date=date(2026, 1, 31),
                        initial_cash=D(1000),
                        status="failed",
                    )
                )
            db.commit()
            statements = []
            event.listen(
                engine,
                "before_cursor_execute",
                lambda conn, cursor, statement, parameters, context, many: statements.append(
                    statement
                ),
            )
            monkeypatch.setattr(repository, "MAX_RUN_JSON", 100)
            loaded, errors = repository.SensitivityRepository().load(db, [1, 2, 3])
            assert list(loaded) == [1] and set(errors) == {2, 3}
            assert loaded[1]["parameters"] == {"top_n": 10}
            assert loaded[1]["total_return"] is None  # outer join retains missing performance
            assert len(statements) == 2 and "length" in statements[0].lower()
            assert all(
                "backtest_equity" not in sql and "backtest_trade" not in sql for sql in statements
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("ids", [[1, 1], [1, True], [1, "2"], [1, 0]])
def test_request_rejects_ambiguous_ids(ids):
    with pytest.raises(ValidationError):
        SensitivityRequest(run_ids=ids, baseline_run_id=1, axes=["top_n"])


def test_api_decimal_strings_validation_and_busy(monkeypatch):
    monkeypatch.setattr(
        backtests,
        "sensitivity_service",
        SensitivityService(lambda _: ({1: run(), 2: run(20, "0")}, {})),
    )
    client = TestClient(app)
    payload = dict(run_ids=[1, 2], baseline_run_id=1, axes=["top_n"])
    response = client.post("/api/v1/backtests/runs/sensitivity", json=payload)
    assert response.status_code == 200
    assert response.json()["cells"][1]["total_return"] == "0"
    assert (
        client.post("/api/v1/backtests/runs/sensitivity", json={**payload, "axes": []}).status_code
        == 422
    )

    def busy(_):
        raise DuckDbWriteLockTimeout("研究库忙")

    monkeypatch.setattr(backtests, "sensitivity_service", SensitivityService(busy))
    assert client.post("/api/v1/backtests/runs/sensitivity", json=payload).status_code == 409
