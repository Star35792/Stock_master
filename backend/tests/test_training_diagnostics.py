from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes import backtests
from app.database.research_session import initialize_research_database
from app.main import app
from app.models.strategy import BacktestRun, TrainingArtifact
from app.schemas.strategy import RollingFactorSelectionBacktestRequest
from app.services.training_diagnostics import (
    DailyRankICEvidence,
    TrainingDiagnosticsPolicy,
    _benjamini_hochberg,
    build_training_diagnostics,
)
from app.services.training_diagnostics_report import (
    MAX_ARTIFACT_SNAPSHOT_CHARS,
    TrainingDiagnosticsReportService,
    TrainingDiagnosticsRunNotFound,
)
from app.services.training_factor_selector import TrainingSelectionPolicy, select_training_factors
from app.strategies import FactorScoreSpec

D = Decimal
START = date(2026, 1, 1)


def evidence(values: list[str | None]) -> list[DailyRankICEvidence]:
    return [
        DailyRankICEvidence(
            trade_date=START + timedelta(days=index),
            rank_ic=None if value is None else D(value),
            common_symbol_count=0 if value is None else 20,
            status="insufficient_pairs" if value is None else "recorded",
        )
        for index, value in enumerate(values)
    ]


def test_hac_uses_fixed_date_grid_and_bh_uses_declared_family() -> None:
    candidates = [FactorScoreSpec("a", "v1"), FactorScoreSpec("b", "v1")]
    values = ["0.20", None, "-0.10", None] * 5
    report = build_training_diagnostics(
        policy=TrainingDiagnosticsPolicy(),
        candidates=candidates,
        eligible_dates=[START + timedelta(days=index) for index in range(len(values))],
        price_dates=[START + timedelta(days=index) for index in range(len(values) + 1)],
        evidence_by_identity={
            ("a", "v1"): evidence(values),
            ("b", "v1"): evidence(["0.10", "-0.10"] * 10),
        },
        qfq_closes={"A": {START: D("10")}},
        price_source={"release_tag": "r1"},
    )
    first, second = report["factors"]
    assert first["hac_pair_count"] == 0  # missing grid dates are never compressed
    assert first["status"] == "recorded"
    assert second["status"] == "recorded"
    assert report["family"] == {
        "declared_count": 2,
        "testable_count": 2,
        "unavailable_count": 0,
        "correction": "benjamini_hochberg_declared_family_v1",
        "alpha": "0.05",
    }
    assert report["research_only"] is True
    assert report["selection_unchanged"] is True
    assert report["qualification_unchanged"] is True


def test_hac_zero_variance_and_too_few_observations_fail_closed() -> None:
    candidates = [FactorScoreSpec("flat", "v1"), FactorScoreSpec("short", "v1")]
    report = build_training_diagnostics(
        policy=TrainingDiagnosticsPolicy(),
        candidates=candidates,
        eligible_dates=[START + timedelta(days=index) for index in range(12)],
        price_dates=[START + timedelta(days=index) for index in range(13)],
        evidence_by_identity={
            ("flat", "v1"): evidence(["0.1"] * 12),
            ("short", "v1"): evidence(["0.2"] * 9 + [None] * 3),
        },
        qfq_closes={},
        price_source={},
    )
    factors = {item["factor_name"]: item for item in report["factors"]}
    assert factors["flat"]["reason"] == "non_positive_hac_variance"
    assert factors["short"]["reason"] == "insufficient_observations(9)"
    assert all(item["p_value"] is None and item["q_value"] is None for item in factors.values())
    assert report["status"] == "insufficient"


def test_bh_is_order_invariant_monotone_and_counts_unavailable_declared_candidates() -> None:
    values = {("b", "v1"): D("0.04"), ("a", "v1"): D("0.01")}
    adjusted = _benjamini_hochberg(values, declared_count=4)
    assert adjusted[("a", "v1")] == D("0.0400000000")
    assert adjusted[("b", "v1")] == D("0.0800000000")
    reversed_adjusted = _benjamini_hochberg(dict(reversed(list(values.items()))), declared_count=4)
    assert adjusted == reversed_adjusted


def test_diagnostics_policy_requires_selection_and_enforces_artifact_budget() -> None:
    payload = dict(
        exchange="SH",
        start_date="2025-01-01",
        end_date="2026-01-01",
        training_days=60,
        test_days=20,
        step_days=80,
        name="diagnostic",
        factor_specs=[{"factor_name": "a", "factor_version": "v1"}],
        top_n=1,
        initial_cash="1000",
        code_version="source",
        data_version="data",
        training_diagnostics_policy="training_ic_hac_bh_v1",
    )
    with pytest.raises(ValidationError, match="requires selection_policy"):
        RollingFactorSelectionBacktestRequest.model_validate(payload)
    payload["selection_policy"] = "training_ic_v1"
    assert RollingFactorSelectionBacktestRequest.model_validate(payload)
    payload["training_days"] = 253
    with pytest.raises(ValidationError, match="at most 252"):
        RollingFactorSelectionBacktestRequest.model_validate(payload)


def test_selector_records_diagnostics_without_changing_selection_result() -> None:
    open_dates = [START + timedelta(days=index) for index in range(12)]
    spec = FactorScoreSpec("alpha", "v1")
    rows = [
        SimpleNamespace(symbol=symbol, trade_date=trade_date, value=D(value), run_id=1)
        for trade_date in open_dates
        for symbol, value in (("A", "1"), ("B", "2"), ("C", "3"))
    ]
    closes = {symbol: {open_dates[0]: D("10")} for symbol in ("A", "B", "C")}
    for index in range(1, len(open_dates)):
        rates = (
            {"A": D("1.01"), "B": D("1.02"), "C": D("1.03")}
            if index % 2
            else {"A": D("1.01"), "B": D("1.03"), "C": D("1.02")}
        )
        for symbol in closes:
            closes[symbol][open_dates[index]] = (
                closes[symbol][open_dates[index - 1]] * rates[symbol]
            )
    policy = TrainingSelectionPolicy(
        min_ic_observations=5,
        min_surviving_factors=1,
        min_abs_mean_rank_ic=D("0.01"),
        min_abs_ic_ir=D("0.01"),
        min_positive_rate=D("0.5"),
    )
    base = select_training_factors(
        policy=policy,
        candidates=[spec],
        rows_by_spec={spec: rows},
        qfq_closes=closes,
        training_open_dates=open_dates,
        training_start_date=open_dates[0],
        training_end_date=open_dates[-1],
    )
    diagnosed = select_training_factors(
        policy=policy,
        candidates=[spec],
        rows_by_spec={spec: rows},
        qfq_closes=closes,
        training_open_dates=open_dates,
        training_start_date=open_dates[0],
        training_end_date=open_dates[-1],
        price_source={"release_tag": "r1"},
        diagnostics_policy=TrainingDiagnosticsPolicy(),
    )
    assert base.selected_specs == diagnosed.selected_specs
    assert base.stats == diagnosed.stats
    assert "diagnostics" not in base.summary()
    diagnostics = diagnosed.summary()["diagnostics"]
    assert diagnostics["contract_version"] == "training_ic_hac_bh_v1"
    assert len(diagnostics["factors"][0]["rank_ic_series"]) == len(open_dates) - 1
    assert diagnostics["price_snapshot"]["row_count"] == 3 * len(open_dates)


def report_service(header: dict | None, artifact=None) -> TrainingDiagnosticsReportService:
    backtest_repository = SimpleNamespace(training_diagnostics_header=Mock(return_value=header))
    artifact_repository = SimpleNamespace(get_by_id=Mock(return_value=artifact))
    return TrainingDiagnosticsReportService(
        backtest_repository=backtest_repository,
        artifact_repository=artifact_repository,
    )


def test_report_never_backfills_old_or_oversized_artifact() -> None:
    old = SimpleNamespace(
        id=8,
        artifact_hash="a" * 64,
        training_input_snapshot={"contract_version": "rolling_training_input_v2", "selection": {}},
    )
    report = report_service(
        {"run_id": 1, "training_artifact_id": 8, "artifact_snapshot_chars": 100}, old
    ).get_report(None, run_id=1)
    assert report.status == "not_recorded"
    assert report.reason == "insufficient_artifact_evidence"

    service = report_service(
        {
            "run_id": 1,
            "training_artifact_id": 8,
            "artifact_snapshot_chars": MAX_ARTIFACT_SNAPSHOT_CHARS + 1,
        },
        old,
    )
    report = service.get_report(None, run_id=1)
    assert report.status == "unsupported"
    service.artifact_repository.get_by_id.assert_not_called()


def test_report_missing_run_and_endpoint_mapping(monkeypatch) -> None:
    with pytest.raises(TrainingDiagnosticsRunNotFound):
        report_service(None).get_report(None, run_id=99)
    monkeypatch.setattr(
        backtests.training_diagnostics_service,
        "get_report",
        Mock(side_effect=TrainingDiagnosticsRunNotFound("backtest run not found")),
    )
    response = TestClient(app).get("/api/v1/backtests/runs/99/training-diagnostics")
    assert response.status_code == 404
    assert response.json()["detail"] == "backtest run not found"


def test_repository_projection_reads_bound_diagnostics_without_current_data(tmp_path) -> None:
    engine = create_engine(f"duckdb:///{tmp_path / 'diagnostics.duckdb'}")
    initialize_research_database(engine)
    diagnostics = build_training_diagnostics(
        policy=TrainingDiagnosticsPolicy(),
        candidates=[FactorScoreSpec("alpha", "v1")],
        eligible_dates=[START + timedelta(days=index) for index in range(12)],
        price_dates=[START + timedelta(days=index) for index in range(13)],
        evidence_by_identity={
            ("alpha", "v1"): evidence(["0.1", "0.2"] * 6),
        },
        qfq_closes={},
        price_source={"release_tag": "r1"},
    )
    with Session(engine) as db:
        artifact = TrainingArtifact(
            window_id="window|diag-training_ic_hac_bh_v1-test",
            strategy_name="factor_selection",
            factor_specs=[{"factor_name": "alpha", "factor_version": "v1"}],
            training_start_date=START,
            training_end_date=START + timedelta(days=12),
            test_start_date=START + timedelta(days=13),
            test_end_date=START + timedelta(days=20),
            training_input_snapshot={
                "contract_version": "rolling_training_input_v2",
                "selection": {"diagnostics": diagnostics},
            },
            artifact_hash="b" * 64,
            code_version="source",
            data_version="data",
        )
        db.add(artifact)
        db.flush()
        run = BacktestRun(
            name="diagnostic-run",
            strategy_name="factor_selection",
            parameters={},
            code_version="source",
            data_version="data",
            start_date=START + timedelta(days=13),
            end_date=START + timedelta(days=20),
            initial_cash=D("1000"),
            training_artifact_id=artifact.id,
            status="success",
        )
        db.add(run)
        db.commit()
        report = TrainingDiagnosticsReportService().get_report(db, run_id=run.id)
        assert report.status == "recorded"
        assert report.training_artifact_id == artifact.id
        assert report.factors[0].factor_name == "alpha"
        assert report.selection_unchanged is True
