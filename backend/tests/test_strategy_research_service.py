from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.backtest import DailyEventBacktestEngine
from app.schemas.strategy import QuantileBacktestRequest
from app.services import StrategyResearchService
from app.services.training_factor_selector import TrainingSelectionPolicy
from app.strategies import FactorScoreSpec, WeightingMode


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
            return self._rows_for_range(start_date, end_date, base_value=Decimal("1"), run_id=11)
        if factor_name == "quality_20":
            return self._rows_for_range(start_date, end_date, base_value=Decimal("3"), run_id=12)
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
        initial_cash = kwargs["initial_cash"]
        start_date = kwargs["start_date"]
        end_date = kwargs["end_date"]
        # Build a minimal but well-shaped equity curve + trades so the rolling
        # combined-curve stitching path has real (non-overlapping) points to
        # concatenate. Each window closes 1% above its opening cash.
        equity_curve = [
            SimpleNamespace(trade_date=start_date, total_equity=initial_cash),
            SimpleNamespace(
                trade_date=end_date, total_equity=initial_cash * Decimal("1.01")
            ),
        ]
        trades = [
            SimpleNamespace(
                quantity=100,
                fill_price=Decimal("10"),
                commission=Decimal("0.3"),
            )
        ]
        return SimpleNamespace(
            run_id=7 + len(self.calls) - 1,
            result=SimpleNamespace(
                initial_cash=initial_cash,
                final_equity=initial_cash * Decimal("1.01"),
                total_return=Decimal("0.01"),
                trades=trades,
                equity_curve=equity_curve,
                positions=[SimpleNamespace(trade_date=end_date, symbol="AAA")],
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
    assert contract["strategy_input_snapshot"]["contract_version"] == ("strategy_factor_frame_v1")
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

    assert len(results.windows) == 4
    assert [result.window.index for result in results.windows] == [0, 1, 2, 3]
    assert all(result.window.execution_status == "run_completed" for result in results.windows)
    assert len(training_artifact_repository.artifacts) == 4
    assert len(backtest_service.calls) == 4
    # Each window's test-period equity is stitched into one combined curve.
    assert results.combined is not None
    assert results.combined.window_count == 4
    assert len(results.combined.trade_dates) == 8
    assert results.combined.total_return > 0
    assert "non_tradable" in results.declarations

    first_artifact = training_artifact_repository.artifacts[0]
    assert first_artifact.window_id == ("rolling:SH:2026-01-02:2026-01-06:2026-01-07:2026-01-08")
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
                    trade_date=trade_date,
                    symbol="AAA",
                    value=Decimal("1") if trade_date < week_three_start else Decimal("3"),
                    run_id=run_id,
                )
            )
            rows.append(
                SimpleNamespace(
                    trade_date=trade_date,
                    symbol="BBB",
                    value=bbb,
                    run_id=run_id,
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


def test_rolling_selection_requires_warm_research_source() -> None:
    service = StrategyResearchService(
        factor_value_repository=FakeFactorValueRepository(),
        security_repository=FakeSecurityRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
        backtest_service=FakeBacktestService(),
        training_artifact_repository=FakeTrainingArtifactRepository(),
    )
    service.research_read_source = "control"

    with pytest.raises(ValueError, match="requires research_read_source=warm"):
        service.run_rolling_factor_selection(
            object(),
            exchange="SH",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 16),
            training_days=3,
            test_days=2,
            step_days=2,
            name="selection-control-rejected",
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
            selection_policy=TrainingSelectionPolicy(),
        )


def test_rolling_with_selection_binds_artifact_and_selected_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import strategy_research as strategy_research_module
    from app.services.training_factor_selector import TrainingSelectionResult

    spec = FactorScoreSpec("momentum_20", "v1-qfq")
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

    def fake_select_training_factors(**kwargs):
        candidates = kwargs["candidates"]
        selected = [
            FactorScoreSpec(
                candidates[0].factor_name,
                candidates[0].factor_version,
                weight=Decimal("1"),
                higher_is_better=not candidates[0].higher_is_better,
            )
        ]
        return TrainingSelectionResult(
            policy=kwargs["policy"],
            config_hash=kwargs["policy"].config_hash(candidates),
            training_start_date=kwargs["training_start_date"],
            training_end_date=kwargs["training_end_date"],
            training_open_dates=3,
            selected_specs=selected,
            stats=[],
            dropped_candidates=[],
            price_source={"prices_qfq_path": "fake", "release_tag": "fake"},
        )

    monkeypatch.setattr(
        strategy_research_module, "select_training_factors", fake_select_training_factors
    )
    monkeypatch.setattr(
        service,
        "_load_qfq_closes",
        lambda db, *, start_date, end_date: (
            {"AAA": {}, "BBB": {}},
            {"prices_qfq_path": "fake", "release_tag": "fake"},
        ),
    )

    policy = TrainingSelectionPolicy()
    results = service.run_rolling_factor_selection(
        object(),
        exchange="SH",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 16),
        training_days=3,
        test_days=2,
        step_days=2,
        name="rolling-selection",
        factor_specs=[spec],
        top_n=1,
        allowed_security_types={"stock"},
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc123",
        data_version="market-qfq-20260720",
        selection_policy=policy,
    )

    config_hash = policy.config_hash([spec])
    base_id = "rolling:SH:2026-01-02:2026-01-06:2026-01-07:2026-01-08"
    suffixed_id = f"{base_id}|sel-training_ic_v1-{config_hash}"
    assert len(results.windows) == 4
    assert results.windows[0].window.window_id == suffixed_id
    assert results.windows[0].selection_summary is not None
    assert results.windows[0].selection_summary["price_source"] == {
        "prices_qfq_path": "fake",
        "release_tag": "fake",
    }

    artifact = training_artifact_repository.artifacts[0]
    assert artifact.window_id == suffixed_id
    snapshot = artifact.training_input_snapshot
    assert snapshot["contract_version"] == "rolling_training_input_v2"
    assert snapshot["selection"]["selected"][0]["higher_is_better"] is False
    assert snapshot["factor_specs"][0]["higher_is_better"] is True

    call = backtest_service.calls[0]
    contract = call["research_contract"]
    assert contract["validation_window_id"] == suffixed_id
    # The contract keeps the candidate pool in factor_specs for validation
    # comparability; the window selection travels in dedicated keys.
    assert contract["factor_specs"][0]["higher_is_better"] is True
    assert contract["selection_policy"]["config_hash"] == config_hash
    assert contract["selection_result"]["selected"][0]["higher_is_better"] is False
    assert contract["validation_window_id"] == suffixed_id


def test_run_factor_selection_passes_quantile_backtest_to_engine() -> None:
    factor_value_repository = FakeFactorValueRepository()
    backtest_service = FakeBacktestService()
    service = StrategyResearchService(
        factor_value_repository=factor_value_repository,
        security_repository=FakeSecurityRepository(),
        backtest_service=backtest_service,
    )

    service.run_factor_selection(
        object(),
        name="quantile-wire",
        factor_specs=[FactorScoreSpec("quality_20", "v1-qfq", weight=Decimal("1"))],
        top_n=1,
        allowed_security_types={"stock"},
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc",
        data_version="dv",
        quantile_backtest=QuantileBacktestRequest(
            split_factor_name="momentum_20",
            split_factor_version="v1-qfq",
            n_layers=2,
        ),
    )

    kwargs = backtest_service.kwargs
    assert kwargs is not None
    assert kwargs["quantile_meta"] is not None
    assert kwargs["quantile_meta"].split_factor_name == "momentum_20"
    assert kwargs["quantile_meta"].n_layers == 2
    # Two layers, each carrying the single 2026-07-01 decision-date signal.
    assert len(kwargs["quantile_layer_signals"]) == 2
    for layer in kwargs["quantile_layer_signals"]:
        assert len(layer) == 1
        assert layer[0].signal_date == date(2026, 7, 1)
        # Equal-weight fully invested: one symbol per layer -> weight 1.0.
        assert layer[0].target_weight == Decimal("1")


def test_quantile_request_rejects_unsupported_weighting_mode() -> None:
    with pytest.raises(
        ValueError,
        match="quantile backtest only supports weighting_mode=equal_weight_fully_invested",
    ):
        QuantileBacktestRequest(
            split_factor_name="momentum_20",
            split_factor_version="v1-qfq",
            n_layers=2,
            weighting_mode=WeightingMode.SCORE_WEIGHTED,
        )


def test_quantile_universe_respects_allowed_security_types() -> None:
    class MixedSecurityFactorRepository(FakeFactorValueRepository):
        def list_for_cross_section(
            self, db: object, **kwargs: object
        ) -> list[SimpleNamespace]:
            trade_date = date(2026, 7, 1)
            return [
                SimpleNamespace(trade_date=trade_date, symbol="AAA", value=Decimal("1")),
                SimpleNamespace(trade_date=trade_date, symbol="BBB", value=Decimal("2")),
                SimpleNamespace(trade_date=trade_date, symbol="ETF", value=Decimal("3")),
                SimpleNamespace(
                    trade_date=trade_date,
                    symbol="UNKNOWN",
                    value=Decimal("4"),
                ),
            ]

    class MixedSecurityRepository(FakeSecurityRepository):
        def get_security_types(
            self, db: object, symbols: set[str]
        ) -> dict[str, str]:
            return {"AAA": "stock", "BBB": "stock", "ETF": "etf"}

    backtest_service = FakeBacktestService()
    service = StrategyResearchService(
        factor_value_repository=MixedSecurityFactorRepository(),
        security_repository=MixedSecurityRepository(),
        backtest_service=backtest_service,
    )

    service.run_factor_selection(
        object(),
        name="quantile-stock-only",
        factor_specs=[FactorScoreSpec("quality_20", "v1-qfq", weight=Decimal("1"))],
        top_n=1,
        allowed_security_types={"stock"},
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc",
        data_version="dv",
        quantile_backtest=QuantileBacktestRequest(
            split_factor_name="momentum_20",
            split_factor_version="v1-qfq",
            n_layers=2,
        ),
    )

    kwargs = backtest_service.kwargs
    assert kwargs is not None
    quantile_symbols = {
        signal.symbol
        for layer in kwargs["quantile_layer_signals"]
        for signal in layer
    }
    assert quantile_symbols == {"AAA", "BBB"}


def test_quantile_layer_signals_exit_rotated_and_missing_buckets() -> None:
    first = date(2026, 7, 1)
    second = date(2026, 7, 2)
    missing = date(2026, 7, 3)

    class RotatingQuantileRepository(FakeFactorValueRepository):
        def list_for_cross_section(
            self, db: object, **kwargs: object
        ) -> list[SimpleNamespace]:
            return [
                SimpleNamespace(trade_date=first, symbol="AAA", value=Decimal("1")),
                SimpleNamespace(trade_date=first, symbol="BBB", value=Decimal("2")),
                SimpleNamespace(trade_date=second, symbol="AAA", value=Decimal("2")),
                SimpleNamespace(trade_date=second, symbol="BBB", value=Decimal("1")),
            ]

    service = StrategyResearchService(
        factor_value_repository=RotatingQuantileRepository(),
        backtest_service=FakeBacktestService(),
    )
    layer_signals, _ = service._build_quantile_layer_signals(
        object(),
        quantile_backtest=QuantileBacktestRequest(
            split_factor_name="momentum_20",
            split_factor_version="v1-qfq",
            n_layers=2,
        ),
        universe={"AAA", "BBB"},
        invested_weight=Decimal("1"),
        start_date=first,
        end_date=missing,
        decision_dates={first, second, missing},
    )

    assert [
        (signal.signal_date, signal.symbol, signal.target_weight)
        for signal in layer_signals[0]
    ] == [
        (first, "AAA", Decimal("1")),
        (second, "AAA", Decimal("0")),
        (second, "BBB", Decimal("1")),
        (missing, "BBB", Decimal("0")),
    ]
    assert [
        (signal.signal_date, signal.symbol, signal.target_weight)
        for signal in layer_signals[1]
    ] == [
        (first, "BBB", Decimal("1")),
        (second, "BBB", Decimal("0")),
        (second, "AAA", Decimal("1")),
        (missing, "AAA", Decimal("0")),
    ]

    final_day = date(2026, 7, 6)
    calendar = [first, second, missing, final_day]
    bars = [
        SimpleNamespace(
            trade_date=trade_date,
            symbol=symbol,
            open=Decimal("10"),
            close=Decimal("10"),
        )
        for trade_date in calendar
        for symbol in ("AAA", "BBB")
    ]
    for layer_index, expected_rotated_symbol in ((0, "BBB"), (1, "AAA")):
        result = DailyEventBacktestEngine(
            initial_cash=Decimal("10000"),
            commission_rate=Decimal(),
            slippage_bps=Decimal(),
            lot_size=100,
        ).run(
            bars=bars,
            signals=layer_signals[layer_index],
            execution_calendar={"AAA": calendar, "BBB": calendar},
        )
        assert {
            row.symbol for row in result.positions if row.trade_date == missing
        } == {expected_rotated_symbol}
        assert not [row for row in result.positions if row.trade_date == final_day]
        assert [trade.side for trade in result.trades] == ["buy", "sell", "buy", "sell"]


def test_run_factor_selection_default_quantile_not_passed() -> None:
    """Regression lock: when quantile_backtest is omitted the engine receives no
    quantile signals/meta, preserving drift comparability of parameters."""
    factor_value_repository = FakeFactorValueRepository()
    backtest_service = FakeBacktestService()
    service = StrategyResearchService(
        factor_value_repository=factor_value_repository,
        security_repository=FakeSecurityRepository(),
        backtest_service=backtest_service,
    )

    service.run_factor_selection(
        object(),
        name="no-quantile",
        factor_specs=[FactorScoreSpec("quality_20", "v1-qfq", weight=Decimal("1"))],
        top_n=1,
        allowed_security_types={"stock"},
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
        adjust_type="qfq",
        initial_cash=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc",
        data_version="dv",
    )

    kwargs = backtest_service.kwargs
    assert kwargs is not None
    assert kwargs["quantile_layer_signals"] is None
    assert kwargs["quantile_meta"] is None


def test_rolling_factor_selection_passes_quantile_per_window() -> None:
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
        name="rolling-quantile",
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
        quantile_backtest=QuantileBacktestRequest(
            split_factor_name="quality_20",
            split_factor_version="v1-qfq",
            n_layers=2,
        ),
    )

    assert len(results.windows) == 4
    for call in backtest_service.calls:
        assert call["quantile_meta"] is not None
        assert call["quantile_meta"].split_factor_name == "quality_20"
        assert len(call["quantile_layer_signals"]) == 2
    assert results.combined is not None
    assert results.combined.window_count == 4
