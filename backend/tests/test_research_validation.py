from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.research_validation import ResearchValidationReportService


def _parameters(
    *,
    validation_mode: str = "rolling",
    training_start: str,
    training_end: str,
    test_start: str,
    test_end: str,
    top_n: int = 10,
    benchmark: str | None = None,
) -> dict:
    return {
        "strategy_name": "factor_selection",
        "factor_specs": [
            {
                "factor_name": "momentum_20",
                "factor_version": "v1-qfq",
                "weight": "1",
                "higher_is_better": True,
            }
        ],
        "top_n": top_n,
        "signal_lag": 1,
        "rebalance_rule": "daily",
        "holding_period": 1,
        "benchmark": benchmark,
        "allowed_security_types": ["stock"],
        "adjust_type": "qfq",
        "execution_adjust_type": "none",
        "corporate_action_policy": "reject",
        "initial_cash": "100000",
        "commission_rate": "0.0003",
        "slippage_bps": "2",
        "lot_size": 100,
        "validation_mode": validation_mode,
        "training_start_date": training_start,
        "training_end_date": training_end,
        "test_start_date": test_start,
        "test_end_date": test_end,
    }


def _run(
    run_id: int,
    *,
    status: str,
    training_start: str,
    training_end: str,
    test_start: str,
    test_end: str,
    top_n: int = 10,
    benchmark: str | None = None,
):
    return SimpleNamespace(
        id=run_id,
        strategy_name="factor_selection",
        parameters=_parameters(
            training_start=training_start,
            training_end=training_end,
            test_start=test_start,
            test_end=test_end,
            top_n=top_n,
            benchmark=benchmark,
        ),
        code_version="code-v1",
        data_version="data-v1",
        start_date=date.fromisoformat(test_start),
        end_date=date.fromisoformat(test_end),
        status=status,
        error_message="execution failed" if status == "failed" else None,
    )


def _performance(total_return: str, drawdown: str, sharpe: str | None):
    return SimpleNamespace(
        total_return=Decimal(total_return),
        annualized_return=Decimal(total_return),
        maximum_drawdown=Decimal(drawdown),
        annualized_volatility=Decimal("0.20"),
        sharpe_ratio=None if sharpe is None else Decimal(sharpe),
        calmar_ratio=Decimal("1.0"),
        daily_win_rate=Decimal("0.55"),
        profit_loss_ratio=Decimal("1.2"),
        turnover_rate=Decimal("0.30"),
        total_commission=Decimal("12.5"),
    )


class FakeBacktestRepository:
    def __init__(self, runs: list[SimpleNamespace]) -> None:
        self.runs = {run.id: run for run in runs}

    def get_run(self, db: object, *, run_id: int):
        return self.runs.get(run_id)

    def list_trades(self, db: object, **kwargs: object):
        return [], int(kwargs["run_id"]) + 1


class FakePerformanceRepository:
    def __init__(self, rows: dict[int, SimpleNamespace]) -> None:
        self.rows = rows

    def get_by_run_id(self, db: object, *, run_id: int):
        return self.rows.get(run_id)


class NoBenchmarkQueryService:
    def get_benchmark_comparison(self, db: object, *, run_id: int):
        raise AssertionError("benchmark query must not run when benchmark is absent")


class PartialBenchmarkQueryService:
    def get_benchmark_comparison(self, db: object, *, run_id: int):
        if run_id == 2:
            raise ValueError("trusted benchmark data is insufficient")
        return SimpleNamespace(
            benchmark_total_return=Decimal("0.04"),
            excess_return=Decimal("0.06"),
        )


def test_validation_report_keeps_failed_windows_and_aggregates_successes() -> None:
    runs = [
        _run(
            1,
            status="success",
            training_start="2026-01-02",
            training_end="2026-03-31",
            test_start="2026-04-01",
            test_end="2026-04-30",
        ),
        _run(
            2,
            status="failed",
            training_start="2026-02-02",
            training_end="2026-04-30",
            test_start="2026-05-01",
            test_end="2026-05-31",
        ),
        _run(
            3,
            status="success",
            training_start="2026-03-02",
            training_end="2026-05-31",
            test_start="2026-06-01",
            test_end="2026-06-30",
        ),
    ]
    service = ResearchValidationReportService(
        backtest_repository=FakeBacktestRepository(runs),
        performance_repository=FakePerformanceRepository(
            {
                1: _performance("0.10", "-0.05", "1.5"),
                3: _performance("-0.02", "-0.08", None),
            }
        ),
        backtest_query_service=NoBenchmarkQueryService(),
    )

    result = service.build_report(object(), run_ids=[1, 2, 3])

    assert result.window_count == 3
    assert result.successful_window_count == 2
    assert result.failed_window_count == 1
    assert result.success_ratio == Decimal(2) / Decimal(3)
    assert [window.status for window in result.windows] == [
        "success",
        "failed",
        "success",
    ]
    assert result.windows[1].error_message == "execution failed"
    assert result.aggregate.return_sample_count == 2
    assert result.aggregate.mean_total_return == Decimal("0.04")
    assert result.aggregate.median_total_return == Decimal("0.04")
    assert result.aggregate.worst_maximum_drawdown == Decimal("-0.08")
    assert result.aggregate.sharpe_sample_count == 1
    assert result.aggregate.total_commission == Decimal("25.0")
    assert result.aggregate.total_trade_count == 6
    assert result.training_artifact_verified is False
    assert result.compounded_return_available is False


def test_validation_report_marks_overlapping_test_windows_without_compounding() -> None:
    runs = [
        _run(
            1,
            status="success",
            training_start="2026-01-01",
            training_end="2026-03-31",
            test_start="2026-04-01",
            test_end="2026-04-30",
        ),
        _run(
            2,
            status="success",
            training_start="2026-01-15",
            training_end="2026-04-14",
            test_start="2026-04-15",
            test_end="2026-05-15",
        ),
    ]
    service = ResearchValidationReportService(
        backtest_repository=FakeBacktestRepository(runs),
        performance_repository=FakePerformanceRepository(
            {
                1: _performance("0.10", "-0.05", "1"),
                2: _performance("0.05", "-0.04", "1"),
            }
        ),
        backtest_query_service=NoBenchmarkQueryService(),
    )

    result = service.build_report(object(), run_ids=[1, 2])

    assert result.overlapping_test_windows is True
    assert result.compounded_return_available is False
    assert "not compounded" in result.limitation


def test_validation_report_rejects_incompatible_contracts() -> None:
    runs = [
        _run(
            1,
            status="success",
            training_start="2026-01-01",
            training_end="2026-03-31",
            test_start="2026-04-01",
            test_end="2026-04-30",
        ),
        _run(
            2,
            status="success",
            training_start="2026-02-01",
            training_end="2026-04-30",
            test_start="2026-05-01",
            test_end="2026-05-31",
            top_n=20,
        ),
    ]
    service = ResearchValidationReportService(
        backtest_repository=FakeBacktestRepository(runs),
        performance_repository=FakePerformanceRepository({}),
        backtest_query_service=NoBenchmarkQueryService(),
    )

    with pytest.raises(ValueError, match="top_n"):
        service.build_report(object(), run_ids=[1, 2])


def test_validation_report_rejects_test_window_different_from_run_dates() -> None:
    run = _run(
        1,
        status="success",
        training_start="2026-01-01",
        training_end="2026-03-31",
        test_start="2026-04-01",
        test_end="2026-04-30",
    )
    run.end_date = date(2026, 5, 1)
    run.parameters["validation_mode"] = "out_of_sample"
    service = ResearchValidationReportService(
        backtest_repository=FakeBacktestRepository([run]),
        performance_repository=FakePerformanceRepository({}),
        backtest_query_service=NoBenchmarkQueryService(),
    )

    with pytest.raises(ValueError, match="does not match run dates"):
        service.build_report(object(), run_ids=[1])


def test_validation_report_exposes_partial_benchmark_coverage() -> None:
    runs = [
        _run(
            1,
            status="success",
            training_start="2026-01-01",
            training_end="2026-03-31",
            test_start="2026-04-01",
            test_end="2026-04-30",
            benchmark="000300.SH",
        ),
        _run(
            2,
            status="success",
            training_start="2026-02-01",
            training_end="2026-04-30",
            test_start="2026-05-01",
            test_end="2026-05-31",
            benchmark="000300.SH",
        ),
    ]
    service = ResearchValidationReportService(
        backtest_repository=FakeBacktestRepository(runs),
        performance_repository=FakePerformanceRepository(
            {
                1: _performance("0.10", "-0.05", "1"),
                2: _performance("0.02", "-0.03", "0.5"),
            }
        ),
        backtest_query_service=PartialBenchmarkQueryService(),
    )

    result = service.build_report(object(), run_ids=[1, 2])

    assert result.windows[0].benchmark_status == "available"
    assert result.windows[1].benchmark_status == "unavailable"
    assert result.windows[1].benchmark_error == "trusted benchmark data is insufficient"
    assert result.aggregate.benchmark_sample_count == 1
    assert result.aggregate.benchmark_coverage_ratio == Decimal("0.5")
    assert result.aggregate.mean_excess_return == Decimal("0.06")


@pytest.mark.parametrize("field,baseline,candidate", [
    ("fee_model_version", "linear_commission_v1", "a_share_cost_v1"),
    ("min_commission", "0", "5"),
    ("sell_stamp_duty_rate", "0", "0.0005"),
    ("transfer_fee_rate", "0", "0.00001"),
    ("exchange_fee_rate", "0", "0.0000487"),
])
def test_validation_report_rejects_mixed_fee_contracts(field, baseline, candidate):
    runs = [
        _run(1, status="failed", training_start="2026-01-01", training_end="2026-01-31",
             test_start="2026-02-01", test_end="2026-02-28"),
        _run(2, status="failed", training_start="2026-02-01", training_end="2026-02-28",
             test_start="2026-03-01", test_end="2026-03-31"),
    ]
    runs[0].parameters[field] = baseline
    runs[1].parameters[field] = candidate
    service = ResearchValidationReportService(
        backtest_repository=FakeBacktestRepository(runs),
        performance_repository=FakePerformanceRepository({}),
    )
    with pytest.raises(ValueError, match=field):
        service.build_report(object(), run_ids=[1, 2])
