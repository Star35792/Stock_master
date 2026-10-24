from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import StrategyResearchService
from app.strategies import FactorScoreSpec


class FakeFactorValueRepository:
    _CALENDAR = [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
        date(2026, 1, 12),
        date(2026, 1, 13),
        date(2026, 1, 14),
        date(2026, 1, 15),
        date(2026, 1, 16),
        date(2026, 7, 1),
    ]

    def __init__(self) -> None:
        self.requests: list[dict] = []

    def list_for_cross_section(self, db: object, **kwargs: object) -> list[SimpleNamespace]:
        self.requests.append(kwargs)
        factor_name = kwargs["factor_name"]
        start_date = kwargs["start_date"]
        end_date = kwargs["end_date"]
        if factor_name == "momentum_20":
            return self._rows_for_range(
                start_date, end_date, base_value=Decimal("1"), run_id=11
            )
        if factor_name == "quality_20":
            return self._rows_for_range(
                start_date, end_date, base_value=Decimal("3"), run_id=12
            )
        return []

    def _rows_for_range(
        self,
        start_date: date,
        end_date: date,
        base_value: Decimal,
        run_id: int,
    ) -> list[SimpleNamespace]:
        rows: list[SimpleNamespace] = []
        for trade_date in self._CALENDAR:
            if start_date <= trade_date <= end_date:
                rows.append(
                    SimpleNamespace(
                        trade_date=trade_date,
                        symbol="AAA",
                        value=base_value,
                        run_id=run_id,
                    )
                )
                rows.append(
                    SimpleNamespace(
                        trade_date=trade_date,
                        symbol="BBB",
                        value=base_value + Decimal("1"),
                        run_id=run_id,
                    )
                )
        return rows


class FakeSecurityRepository:
    def get_security_types(self, db: object, symbols: set[str]) -> dict[str, str]:
        return {symbol: "stock" for symbol in symbols}


class FakeTradingCalendarRepository:
    open_dates = [
        date(2026, 1, 2),
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
        date(2026, 1, 8),
        date(2026, 1, 9),
        date(2026, 1, 12),
        date(2026, 1, 13),
        date(2026, 1, 14),
        date(2026, 1, 15),
        date(2026, 1, 16),
    ]

    def list_open_dates(self, db: object, **kwargs: object) -> list[date]:
        return [
            trade_date
            for trade_date in self.open_dates
            if kwargs["start_date"] <= trade_date <= kwargs["end_date"]
        ]


class FakeBacktestService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def kwargs(self) -> dict | None:
        return self.calls[-1] if self.calls else None

    def run(self, db: object, **kwargs: object) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            run_id=7 + len(self.calls) - 1,
            result=SimpleNamespace(
                initial_cash=kwargs["initial_cash"],
                final_equity=Decimal("110000"),
                total_return=Decimal("0.1"),
                trades=[1, 2],
                equity_curve=[1, 2, 3],
                positions=[1],
            ),
        )


def test_strategy_research_service_generates_rolling_windows() -> None:
    service = StrategyResearchService(
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
    )
    windows = service.generate_rolling_windows(
        object(),
        exchange="SH",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 16),
        training_days=3,
        test_days=2,
        step_days=2,
    )

    assert [window.model_dump() for window in windows] == [
        {
            "index": 0,
            "window_id": "rolling:SH:2026-01-02:2026-01-06:2026-01-07:2026-01-08",
            "training_start_date": date(2026, 1, 2),
            "training_end_date": date(2026, 1, 6),
            "test_start_date": date(2026, 1, 7),
            "test_end_date": date(2026, 1, 8),
            "execution_status": "planning_only",
        },
        {
            "index": 1,
            "window_id": "rolling:SH:2026-01-06:2026-01-08:2026-01-09:2026-01-12",
            "training_start_date": date(2026, 1, 6),
            "training_end_date": date(2026, 1, 8),
            "test_start_date": date(2026, 1, 9),
            "test_end_date": date(2026, 1, 12),
            "execution_status": "planning_only",
        },
        {
            "index": 2,
            "window_id": "rolling:SH:2026-01-08:2026-01-12:2026-01-13:2026-01-14",
            "training_start_date": date(2026, 1, 8),
            "training_end_date": date(2026, 1, 12),
            "test_start_date": date(2026, 1, 13),
            "test_end_date": date(2026, 1, 14),
            "execution_status": "planning_only",
        },
        {
            "index": 3,
            "window_id": "rolling:SH:2026-01-12:2026-01-14:2026-01-15:2026-01-16",
            "training_start_date": date(2026, 1, 12),
            "training_end_date": date(2026, 1, 14),
            "test_start_date": date(2026, 1, 15),
            "test_end_date": date(2026, 1, 16),
            "execution_status": "planning_only",
        },
    ]


def test_strategy_research_service_rejects_short_rolling_window_range() -> None:
    service = StrategyResearchService(
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="open trading dates are insufficient"):
        service.generate_rolling_windows(
            object(),
            exchange="SH",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 7),
            training_days=3,
            test_days=2,
            step_days=2,
        )


def test_strategy_research_service_rejects_reversed_rolling_window_range() -> None:
    service = StrategyResearchService(
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="start_date must be earlier"):
        service.generate_rolling_windows(
            object(),
            exchange="SH",
            start_date=date(2026, 1, 16),
            end_date=date(2026, 1, 1),
            training_days=3,
            test_days=2,
            step_days=2,
        )


def test_strategy_research_service_runs_factor_selection_backtest() -> None:
    factor_value_repository = FakeFactorValueRepository()
    backtest_service = FakeBacktestService()
    service = StrategyResearchService(
        factor_value_repository=factor_value_repository,
        security_repository=FakeSecurityRepository(),
        backtest_service=backtest_service,
    )

    result = service.run_factor_selection(
        object(),
        name="factor-selection-smoke",
        strategy_name="factor_selection",
        factor_specs=[
            FactorScoreSpec("quality_20", "v1-qfq", weight=Decimal("0.4")),
            FactorScoreSpec("momentum_20", "v1-qfq", weight=Decimal("0.6")),
        ],
        top_n=1,
        signal_lag=1,
        rebalance_rule="daily",
        holding_period=1,
        benchmark="000300.SH",
        allowed_security_types={"stock"},
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc123",
        data_version="market-qfq-20260720",
    )

    assert result.run_id == 7
    assert backtest_service.kwargs is not None
    assert backtest_service.kwargs["strategy_name"] == "factor_selection"
    assert backtest_service.kwargs["name"] == "factor-selection-smoke"
    assert backtest_service.kwargs["initial_cash"] == Decimal("100000")
    assert factor_value_repository.requests[0]["factor_name"] == "momentum_20"
    assert factor_value_repository.requests[1]["factor_name"] == "quality_20"
    contract = backtest_service.kwargs["research_contract"]
    assert contract["benchmark"] == "000300.SH"
    assert contract["allowed_security_types"] == ["stock"]
    assert contract["strategy_input_snapshot"]["contract_version"] == (
        "strategy_factor_frame_v1"
    )
    assert contract["strategy_input_snapshot"]["as_of_date"] == "2026-07-01"
    assert contract["strategy_input_snapshot"]["row_count"] == 4
    assert contract["strategy_input_snapshot"]["factor_run_ids"] == [11, 12]
    assert len(contract["strategy_input_snapshot"]["sha256"]) == 64
    assert contract["factor_specs"] == [
        {
            "factor_name": "momentum_20",
            "factor_version": "v1-qfq",
            "weight": "0.6",
            "higher_is_better": True,
        },
        {
            "factor_name": "quality_20",
            "factor_version": "v1-qfq",
            "weight": "0.4",
            "higher_is_better": True,
        },
    ]


def test_strategy_research_service_runs_traceable_out_of_sample_window() -> None:
    factor_value_repository = FakeFactorValueRepository()
    backtest_service = FakeBacktestService()
    service = StrategyResearchService(
        factor_value_repository=factor_value_repository,
        security_repository=FakeSecurityRepository(),
        backtest_service=backtest_service,
    )

    service.run_factor_selection(
        object(),
        name="oos-window",
        factor_specs=[FactorScoreSpec("momentum_20", "v1-qfq")],
        top_n=1,
        allowed_security_types={"stock"},
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc123",
        data_version="market-qfq-20260720",
        validation_mode="out_of_sample",
        training_start_date=date(2026, 1, 2),
        training_end_date=date(2026, 6, 30),
    )

    assert factor_value_repository.requests == [
        {
            "factor_name": "momentum_20",
            "factor_version": "v1-qfq",
            "start_date": date(2026, 7, 1),
            "end_date": date(2026, 7, 1),
        }
    ]
    assert backtest_service.kwargs is not None
    assert backtest_service.kwargs["start_date"] == date(2026, 7, 1)
    assert backtest_service.kwargs["end_date"] == date(2026, 7, 1)
    contract = backtest_service.kwargs["research_contract"]
    assert contract["test_start_date"] == "2026-07-01"
    assert contract["test_end_date"] == "2026-07-01"
    assert contract["validation_window_id"] == (
        "out_of_sample:2026-01-02:2026-06-30:2026-07-01:2026-07-01"
    )
    assert contract["training_artifact_verified"] is False
    assert contract["window_binding_status"] == "recorded_only"


def test_strategy_research_service_rejects_overlapping_validation_window() -> None:
    service = StrategyResearchService(
        factor_value_repository=FakeFactorValueRepository(),
        security_repository=FakeSecurityRepository(),
        backtest_service=FakeBacktestService(),
    )

    with pytest.raises(ValueError, match="training period must end before the test period"):
        service.run_factor_selection(
            object(),
            name="overlapping-oos",
            factor_specs=[FactorScoreSpec("momentum_20", "v1-qfq")],
            top_n=1,
            allowed_security_types={"stock"},
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 2),
            adjust_type="qfq",
            initial_cash=Decimal("100000"),
            commission_rate=Decimal("0.0003"),
            slippage_bps=Decimal("2"),
            lot_size=100,
            code_version="abc123",
            data_version="market-qfq-20260720",
            validation_mode="out_of_sample",
            training_start_date=date(2026, 6, 1),
            training_end_date=date(2026, 7, 1),
        )


def test_strategy_research_service_rejects_unbound_rolling_execution() -> None:
    service = StrategyResearchService(
        factor_value_repository=FakeFactorValueRepository(),
        security_repository=FakeSecurityRepository(),
        backtest_service=FakeBacktestService(),
    )

    with pytest.raises(ValueError, match="rolling execution must use run_rolling_factor_selection"):
        service.run_factor_selection(
            object(),
            name="pseudo-rolling",
            factor_specs=[FactorScoreSpec("momentum_20", "v1-qfq")],
            top_n=1,
            allowed_security_types={"stock"},
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 2),
            adjust_type="qfq",
            initial_cash=Decimal("100000"),
            commission_rate=Decimal("0.0003"),
            slippage_bps=Decimal("2"),
            lot_size=100,
            code_version="abc123",
            data_version="market-qfq-20260720",
            validation_mode="rolling",
            training_start_date=date(2026, 6, 1),
            training_end_date=date(2026, 6, 30),
        )


def test_strategy_research_service_rejects_missing_factor_values() -> None:
    service = StrategyResearchService(
        factor_value_repository=FakeFactorValueRepository(),
        security_repository=FakeSecurityRepository(),
        backtest_service=FakeBacktestService(),
    )

    try:
        service.run_factor_selection(
            object(),
            name="missing-factor",
            factor_specs=[FactorScoreSpec("missing", "v1")],
            top_n=1,
            allowed_security_types={"stock"},
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
            adjust_type="qfq",
            initial_cash=Decimal("100000"),
            commission_rate=Decimal("0.0003"),
            slippage_bps=Decimal("2"),
            lot_size=100,
            code_version="abc123",
            data_version="market-qfq-20260720",
        )
    except ValueError as exc:
        assert "factor values not found for missing@v1" in str(exc)
    else:
        raise AssertionError("missing factor values must be rejected")


class FakeTrainingArtifactRepository:
    def __init__(self) -> None:
        self.artifacts: list[SimpleNamespace] = []
        self._next_id = 1

    def get_by_window_id(self, db: object, *, window_id: str) -> SimpleNamespace | None:
        for artifact in self.artifacts:
            if artifact.window_id == window_id:
                return artifact
        return None

    def create(self, db: object, **kwargs: object) -> SimpleNamespace:
        artifact = SimpleNamespace(id=self._next_id, **kwargs)
        self._next_id += 1
        self.artifacts.append(artifact)
        return artifact


def test_strategy_research_service_runs_rolling_factor_selection() -> None:
    factor_value_repository = FakeFactorValueRepository()
    backtest_service = FakeBacktestService()
    training_artifact_repository = FakeTrainingArtifactRepository()
    service = StrategyResearchService(
        factor_value_repository=factor_value_repository,
        security_repository=FakeSecurityRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
        backtest_service=backtest_service,
        training_artifact_repository=training_artifact_repository,
    )

    results = service.run_rolling_factor_selection(
        object(),
        exchange="SH",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 16),
        training_days=3,
        test_days=2,
        step_days=2,
        name="rolling-smoke",
        factor_specs=[FactorScoreSpec("momentum_20", "v1-qfq")],
        top_n=1,
        allowed_security_types={"stock"},
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc123",
        data_version="market-qfq-20260720",
    )

    assert len(results) == 4
    assert [result.window.index for result in results] == [0, 1, 2, 3]
    assert all(
        result.window.execution_status == "run_completed" for result in results
    )
    assert len(training_artifact_repository.artifacts) == 4
    assert len(backtest_service.calls) == 4

    first_artifact = training_artifact_repository.artifacts[0]
    assert first_artifact.window_id == (
        "rolling:SH:2026-01-02:2026-01-06:2026-01-07:2026-01-08"
    )
    assert first_artifact.training_start_date == date(2026, 1, 2)
    assert first_artifact.test_end_date == date(2026, 1, 8)
    assert first_artifact.artifact_hash is not None
    assert len(first_artifact.artifact_hash) == 64
    assert first_artifact.training_input_snapshot["contract_version"] == (
        "rolling_training_input_v1"
    )
    assert first_artifact.training_input_snapshot["factor_frame"]["row_count"] == 6

    first_call = backtest_service.calls[0]
    assert first_call["training_artifact_id"] == 1
    assert first_call["start_date"] == date(2026, 1, 7)
    assert first_call["end_date"] == date(2026, 1, 8)
    contract = first_call["research_contract"]
    assert contract["validation_mode"] == "rolling"
    assert contract["validation_protocol"] == "rolling_walk_forward_v1"
    assert contract["window_binding_status"] == "artifact_bound"
    assert contract["validation_window_id"] == first_artifact.window_id


def test_strategy_research_service_reuses_existing_training_artifact() -> None:
    factor_value_repository = FakeFactorValueRepository()
    backtest_service = FakeBacktestService()
    training_artifact_repository = FakeTrainingArtifactRepository()
    service = StrategyResearchService(
        factor_value_repository=factor_value_repository,
        security_repository=FakeSecurityRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
        backtest_service=backtest_service,
        training_artifact_repository=training_artifact_repository,
    )

    service.run_rolling_factor_selection(
        object(),
        exchange="SH",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 16),
        training_days=3,
        test_days=2,
        step_days=2,
        name="rolling-smoke",
        factor_specs=[FactorScoreSpec("momentum_20", "v1-qfq")],
        top_n=1,
        allowed_security_types={"stock"},
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc123",
        data_version="market-qfq-20260720",
    )
    first_artifact_count = len(training_artifact_repository.artifacts)
    first_calls = len(backtest_service.calls)

    service.run_rolling_factor_selection(
        object(),
        exchange="SH",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 16),
        training_days=3,
        test_days=2,
        step_days=2,
        name="rolling-rerun",
        factor_specs=[FactorScoreSpec("momentum_20", "v1-qfq")],
        top_n=1,
        allowed_security_types={"stock"},
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc123",
        data_version="market-qfq-20260720",
    )

    assert len(training_artifact_repository.artifacts) == first_artifact_count
    assert len(backtest_service.calls) == first_calls + 4


def test_strategy_research_service_rejects_artifact_hash_mismatch() -> None:
    factor_value_repository = FakeFactorValueRepository()
    backtest_service = FakeBacktestService()
    training_artifact_repository = FakeTrainingArtifactRepository()
    service = StrategyResearchService(
        factor_value_repository=factor_value_repository,
        security_repository=FakeSecurityRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
        backtest_service=backtest_service,
        training_artifact_repository=training_artifact_repository,
    )

    service.run_rolling_factor_selection(
        object(),
        exchange="SH",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 16),
        training_days=3,
        test_days=2,
        step_days=2,
        name="rolling-smoke",
        factor_specs=[FactorScoreSpec("momentum_20", "v1-qfq")],
        top_n=1,
        allowed_security_types={"stock"},
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc123",
        data_version="market-qfq-20260720",
    )

    # Simulate backfill: mutate the stored artifact hash so it no longer matches
    # the training input computed from the current factor rows.
    training_artifact_repository.artifacts[0].artifact_hash = "stale-hash"

    with pytest.raises(ValueError, match="content hash does not match"):
        service.run_rolling_factor_selection(
            object(),
            exchange="SH",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 16),
            training_days=3,
            test_days=2,
            step_days=2,
            name="rolling-rerun",
            factor_specs=[FactorScoreSpec("momentum_20", "v1-qfq")],
            top_n=1,
            allowed_security_types={"stock"},
            adjust_type="qfq",
            initial_cash=Decimal("100000"),
            commission_rate=Decimal("0.0003"),
            slippage_bps=Decimal("2"),
            lot_size=100,
            code_version="abc123",
            data_version="market-qfq-20260720",
        )


def test_weekly_rebalance_filters_signals_to_last_open_date_of_week() -> None:
    backtest_service = FakeBacktestService()
    service = StrategyResearchService(
        factor_value_repository=FakeFactorValueRepository(),
        security_repository=FakeSecurityRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        backtest_service=backtest_service,
    )

    service.run_factor_selection(
        object(),
        name="weekly-smoke",
        factor_specs=[FactorScoreSpec("quality_20", "v1-qfq", weight=Decimal("1"))],
        top_n=1,
        rebalance_rule="weekly",
        allowed_security_types={"stock"},
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 12),
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc",
        data_version="dv",
    )

    signals = backtest_service.kwargs["signals"]
    # Fake calendar: 2026-01-05..09 is ISO week 2 (last open 01-09),
    # 2026-01-12 starts week 3 (last open within window is 01-12 itself).
    assert sorted({signal.signal_date for signal in signals}) == [
        date(2026, 1, 9),
        date(2026, 1, 12),
    ]
    contract = backtest_service.kwargs["research_contract"]
    assert contract["rebalance_rule"] == "weekly"
    assert contract["rebalance_interval"] == 1
    assert contract["portfolio_constraints"] is None


def test_every_n_trading_days_rebalance_keeps_every_nth_signal_date() -> None:
    backtest_service = FakeBacktestService()
    service = StrategyResearchService(
        factor_value_repository=FakeFactorValueRepository(),
        security_repository=FakeSecurityRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        backtest_service=backtest_service,
    )

    service.run_factor_selection(
        object(),
        name="every-5d-smoke",
        factor_specs=[FactorScoreSpec("quality_20", "v1-qfq", weight=Decimal("1"))],
        top_n=1,
        rebalance_rule="every_n_trading_days",
        rebalance_interval=5,
        allowed_security_types={"stock"},
        start_date=date(2026, 1, 2),
        end_date=date(2026, 1, 14),
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc",
        data_version="dv",
    )

    signals = backtest_service.kwargs["signals"]
    # Fake calendar open dates from 01-02, every 5th: 01-02, 01-09.
    assert sorted({signal.signal_date for signal in signals}) == [
        date(2026, 1, 2),
        date(2026, 1, 9),
    ]
    contract = backtest_service.kwargs["research_contract"]
    assert contract["rebalance_interval"] == 5


def test_rebalance_interval_rejected_for_non_every_n_rules() -> None:
    service = StrategyResearchService(
        factor_value_repository=FakeFactorValueRepository(),
        security_repository=FakeSecurityRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        backtest_service=FakeBacktestService(),
    )

    with pytest.raises(ValueError, match="rebalance_interval"):
        service.run_factor_selection(
            object(),
            name="bad-interval",
            factor_specs=[FactorScoreSpec("quality_20", "v1-qfq", weight=Decimal("1"))],
            top_n=1,
            rebalance_rule="weekly",
            rebalance_interval=3,
            allowed_security_types={"stock"},
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 12),
            adjust_type="qfq",
            initial_cash=Decimal("100000"),
            commission_rate=Decimal("0.0003"),
            slippage_bps=Decimal("2"),
            lot_size=100,
            code_version="abc",
            data_version="dv",
        )


class _RotatingFactorRepository(FakeFactorValueRepository):
    """Factor values that flip the top-ranked symbol mid-window.

    BBB leads in ISO week 2 (01-05..09); AAA leads in week 3 (01-12..16).
    A weekly strategy must therefore sell BBB and buy AAA on 01-12.
    """

    def _rows_for_range(self, start_date, end_date, base_value, run_id):
        rows: list[SimpleNamespace] = []
        week_three_start = date(2026, 1, 12)
        for trade_date in self._CALENDAR:
            if not start_date <= trade_date <= end_date:
                continue
            bbb = Decimal("2") if trade_date < week_three_start else Decimal("1")
            rows.append(
                SimpleNamespace(
                    trade_date=trade_date, symbol="AAA",
                    value=Decimal("1") if trade_date < week_three_start else Decimal("3"),
                    run_id=run_id,
                )
            )
            rows.append(
                SimpleNamespace(
                    trade_date=trade_date, symbol="BBB", value=bbb, run_id=run_id,
                )
            )
        return rows


def test_weekly_rebalance_emits_exit_signal_on_retained_day() -> None:
    backtest_service = FakeBacktestService()
    service = StrategyResearchService(
        factor_value_repository=_RotatingFactorRepository(),
        security_repository=FakeSecurityRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        backtest_service=backtest_service,
    )

    service.run_factor_selection(
        object(),
        name="weekly-rotation",
        factor_specs=[FactorScoreSpec("quality_20", "v1-qfq", weight=Decimal("1"))],
        top_n=1,
        rebalance_rule="weekly",
        allowed_security_types={"stock"},
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 16),
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc",
        data_version="dv",
    )

    signals = backtest_service.kwargs["signals"]
    by_date: dict[date, dict[str, Decimal]] = {}
    for signal in signals:
        by_date.setdefault(signal.signal_date, {})[signal.symbol] = signal.target_weight
    # Only the last open date of each ISO week carries signals.
    assert sorted(by_date) == [date(2026, 1, 9), date(2026, 1, 16)]
    # Week 2 holds BBB; week 3 must rotate to AAA *and* exit BBB on the
    # retained day — the regression that produced zombie holdings.
    assert by_date[date(2026, 1, 9)] == {"BBB": Decimal("1")}
    assert by_date[date(2026, 1, 16)] == {"AAA": Decimal("1"), "BBB": Decimal("0")}


def test_rolling_rejects_non_daily_rebalance_for_non_sh_exchange() -> None:
    service = StrategyResearchService(
        factor_value_repository=FakeFactorValueRepository(),
        security_repository=FakeSecurityRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        backtest_service=FakeBacktestService(),
    )

    with pytest.raises(ValueError, match="exchange=SH"):
        service.run_rolling_factor_selection(
            object(),
            exchange="HK",
            start_date=date(2026, 1, 2),
            end_date=date(2026, 1, 16),
            training_days=3,
            test_days=2,
            step_days=2,
            name="hk-weekly",
            factor_specs=[FactorScoreSpec("quality_20", "v1-qfq", weight=Decimal("1"))],
            top_n=1,
            rebalance_rule="weekly",
            allowed_security_types={"stock"},
            adjust_type="qfq",
            initial_cash=Decimal("100000"),
            commission_rate=Decimal("0.0003"),
            slippage_bps=Decimal("2"),
            lot_size=100,
            code_version="abc",
            data_version="dv",
        )
