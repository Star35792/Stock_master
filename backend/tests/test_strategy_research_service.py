from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import StrategyResearchService
from app.strategies import FactorScoreSpec


class FakeFactorValueRepository:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    def list_for_cross_section(self, db: object, **kwargs: object) -> list[SimpleNamespace]:
        self.requests.append(kwargs)
        if kwargs["factor_name"] == "momentum_20":
            return [
                SimpleNamespace(
                    trade_date=date(2026, 7, 1),
                    symbol="aaa",
                    value=Decimal("1"),
                    run_id=11,
                ),
                SimpleNamespace(
                    trade_date=date(2026, 7, 1),
                    symbol="BBB",
                    value=Decimal("2"),
                    run_id=11,
                ),
            ]
        if kwargs["factor_name"] == "quality_20":
            return [
                SimpleNamespace(
                    trade_date=date(2026, 7, 1),
                    symbol="AAA",
                    value=Decimal("3"),
                    run_id=12,
                ),
                SimpleNamespace(
                    trade_date=date(2026, 7, 1),
                    symbol="BBB",
                    value=Decimal("1"),
                    run_id=12,
                ),
            ]
        return []


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
        self.kwargs: dict | None = None

    def run(self, db: object, **kwargs: object) -> SimpleNamespace:
        self.kwargs = kwargs
        return SimpleNamespace(
            run_id=7,
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

    with pytest.raises(ValueError, match="rolling execution requires immutable training artifacts"):
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
