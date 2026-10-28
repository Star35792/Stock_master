from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.backtest import TargetWeightSignal
from app.backtest.quantile_result import QuantileBacktestMeta
from app.services import DailyBacktestService
from app.services.backtest import _independent_verification_session


class FakeDailyBarRepository:
    def __init__(self, symbol: str = "AAA.SH") -> None:
        self.kwargs: dict | None = None
        self.symbol = symbol

    def list_for_factor_calculation(
        self,
        db: object,
        **kwargs: object,
    ) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        if kwargs["adjust_type"] == "qfq":
            first_open, first_close = Decimal("5"), Decimal("5")
            second_open, second_close = Decimal("5"), Decimal("5.5")
        else:
            first_open, first_close = Decimal("10"), Decimal("10")
            second_open, second_close = Decimal("10"), Decimal("11")
        return [
            SimpleNamespace(
                symbol=self.symbol,
                security_type="stock",
                trade_date=date(2026, 7, 1),
                open=first_open,
                close=first_close,
            ),
            SimpleNamespace(
                symbol=self.symbol,
                security_type="stock",
                trade_date=date(2026, 7, 2),
                open=second_open,
                close=second_close,
            ),
        ]


class FakeTradingCalendarRepository:
    def __init__(self) -> None:
        self.exchanges: list[str] = []

    def list_open_dates(
        self,
        db: object,
        *,
        exchange: str,
        start_date: date,
        end_date: date,
    ) -> list[date]:
        self.exchanges.append(exchange)
        return [start_date, end_date]


class FakeCorporateActionRepository:
    def __init__(self, actions: list[SimpleNamespace] | None = None) -> None:
        self.actions = actions or []
        self.kwargs: dict | None = None

    def list_for_symbols_date_range(
        self,
        db: object,
        **kwargs: object,
    ) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.actions


class FakeBacktestRepository:
    def __init__(self) -> None:
        self.create_kwargs: dict | None = None
        self.saved_result: object | None = None
        self.failed_message: str | None = None
        self.updated_snapshots: list[dict] = []
        self.updated_parameters: list[dict] = []

    def create_run(self, db: object, **kwargs: object) -> SimpleNamespace:
        self.create_kwargs = kwargs
        return SimpleNamespace(id=1, parameters=kwargs["parameters"])

    def update_data_snapshot(
        self,
        db: object,
        *,
        run: SimpleNamespace,
        data_snapshot: dict,
    ) -> None:
        run.parameters = {**run.parameters, "data_snapshot": data_snapshot}
        self.updated_snapshots.append(data_snapshot)

    def save_result(self, db: object, *, run: object, result: object) -> None:
        self.saved_result = result

    def update_run_parameters(
        self,
        db: object,
        *,
        run: SimpleNamespace,
        extra: dict,
    ) -> None:
        run.parameters = {**run.parameters, **extra}
        self.updated_parameters.append(extra)

    def fail_run(self, db: object, *, run: object, error_message: str) -> None:
        self.failed_message = error_message


def test_backtest_service_reads_trusted_market_and_persists_result() -> None:
    daily_bar_repository = FakeDailyBarRepository()
    backtest_repository = FakeBacktestRepository()
    service = DailyBacktestService(
        daily_bar_repository=daily_bar_repository,
        backtest_repository=backtest_repository,
        corporate_action_repository=FakeCorporateActionRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
    )

    persisted = service.run(
        object(),
        name="smoke",
        strategy_name="unit_test",
        signals=[
            TargetWeightSignal(
                signal_date=date(2026, 7, 1),
                symbol="aaa.sh",
                target_weight=Decimal("1"),
            )
        ],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
        adjust_type="none",
        initial_cash=Decimal("10000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        lot_size=100,
        code_version="abc123",
        data_version="market-none-20260702",
    )

    assert persisted.run_id == 1
    assert persisted.result.final_equity == Decimal("11000")
    assert backtest_repository.saved_result is persisted.result
    assert daily_bar_repository.kwargs is not None
    assert daily_bar_repository.kwargs["symbols"] == ["AAA.SH"]
    assert daily_bar_repository.kwargs["quality_status"] == "trusted"
    assert backtest_repository.create_kwargs is not None
    assert (
        backtest_repository.create_kwargs["parameters"]["signals"][0]["symbol"]
        == "AAA.SH"
    )
    assert backtest_repository.create_kwargs["parameters"]["adjust_type"] == "none"
    assert (
        backtest_repository.create_kwargs["parameters"]["corporate_action_policy"]
        == "reject"
    )
    snapshot = backtest_repository.updated_snapshots[-1]
    assert snapshot["snapshot_version"] == "trusted_market_slice_v1"
    assert snapshot["bar_count"] == 2
    assert snapshot["verification_status"] == "verified"
    assert snapshot["sha256"] == snapshot["post_run_sha256"]
    assert backtest_repository.create_kwargs["parameters"]["engine_version"] == (
        "daily_event_v2"
    )
    assert backtest_repository.create_kwargs["parameters"]["fee_model_version"] == (
        "linear_commission_v1"
    )


def test_backtest_service_validates_signal_boundary() -> None:
    service = DailyBacktestService(
        daily_bar_repository=FakeDailyBarRepository(),
        backtest_repository=FakeBacktestRepository(),
        corporate_action_repository=FakeCorporateActionRepository(),
    )

    with pytest.raises(ValueError, match="signals must not be empty"):
        service.run(
            object(),
            name="empty",
            strategy_name="unit_test",
            signals=[],
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 2),
            adjust_type="none",
            initial_cash=Decimal("10000"),
            commission_rate=Decimal("0"),
            slippage_bps=Decimal("0"),
            lot_size=100,
            code_version="abc123",
            data_version="market-none-20260702",
        )


def test_backtest_service_persists_research_contract() -> None:
    daily_bar_repository = FakeDailyBarRepository()
    backtest_repository = FakeBacktestRepository()
    service = DailyBacktestService(
        daily_bar_repository=daily_bar_repository,
        backtest_repository=backtest_repository,
        corporate_action_repository=FakeCorporateActionRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
    )

    persisted = service.run(
        object(),
        name="contract",
        strategy_name="factor_selection",
        signals=[
            TargetWeightSignal(
                signal_date=date(2026, 7, 1),
                symbol="aaa.sh",
                target_weight=Decimal("1"),
            )
        ],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
        adjust_type="qfq",
        initial_cash=Decimal("10000"),
        commission_rate=Decimal("0.0003"),
        slippage_bps=Decimal("2"),
        lot_size=100,
        code_version="abc123",
        data_version="market-qfq-20260702",
        research_contract={
            "strategy_name": "factor_selection",
            "factor_specs": [{"factor_name": "foo", "factor_version": "v1"}],
            "top_n": 1,
            "signal_lag": 1,
            "rebalance_rule": "daily",
            "holding_period": 1,
            "benchmark": None,
            "allowed_security_types": ["stock"],
            "start_date": "2026-07-01",
            "end_date": "2026-07-02",
            "adjust_type": "qfq",
            "initial_cash": "10000",
            "commission_rate": "0.0003",
            "slippage_bps": "2",
            "lot_size": 100,
            "code_version": "abc123",
            "data_version": "market-qfq-20260702",
        },
    )

    assert backtest_repository.create_kwargs is not None
    assert daily_bar_repository.kwargs is not None
    assert daily_bar_repository.kwargs["adjust_type"] == "none"
    assert persisted.result.trades[0].reference_price == Decimal("10")
    assert backtest_repository.create_kwargs["parameters"]["strategy_name"] == "factor_selection"
    assert backtest_repository.create_kwargs["parameters"]["adjust_type"] == "qfq"
    assert backtest_repository.create_kwargs["parameters"]["execution_adjust_type"] == "none"
    assert (
        backtest_repository.create_kwargs["parameters"]["factor_specs"][0]["factor_name"]
        == "foo"
    )


def test_backtest_service_loads_quantile_symbols_independently() -> None:
    class MultiSymbolDailyBarRepository(FakeDailyBarRepository):
        def __init__(self) -> None:
            super().__init__()
            self.symbol_requests: list[list[str]] = []

        def list_for_factor_calculation(
            self,
            db: object,
            **kwargs: object,
        ) -> list[SimpleNamespace]:
            symbols = list(kwargs["symbols"])
            self.symbol_requests.append(symbols)
            self.kwargs = kwargs
            return [
                SimpleNamespace(
                    symbol=symbol,
                    security_type="stock",
                    trade_date=trade_date,
                    open=Decimal("10"),
                    close=Decimal("10"),
                )
                for trade_date in (date(2026, 7, 1), date(2026, 7, 2))
                for symbol in symbols
            ]

    daily_bar_repository = MultiSymbolDailyBarRepository()
    backtest_repository = FakeBacktestRepository()
    service = DailyBacktestService(
        daily_bar_repository=daily_bar_repository,
        backtest_repository=backtest_repository,
        corporate_action_repository=FakeCorporateActionRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
    )

    persisted = service.run(
        SimpleNamespace(commit=lambda: None),
        name="quantile-symbol-scope",
        strategy_name="factor_selection",
        signals=[TargetWeightSignal(date(2026, 7, 1), "AAA.SH", Decimal("1"))],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
        adjust_type="qfq",
        initial_cash=Decimal("10000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        lot_size=100,
        code_version="abc123",
        data_version="market-qfq-20260702",
        quantile_layer_signals=[
            [TargetWeightSignal(date(2026, 7, 1), "AAA.SH", Decimal("1"))],
            [TargetWeightSignal(date(2026, 7, 1), "BBB.SH", Decimal("1"))],
        ],
        quantile_meta=QuantileBacktestMeta(
            split_factor_name="momentum_20",
            split_factor_version="v1-qfq",
            n_layers=2,
            max_assets_per_layer=None,
            layer_bounds=((Decimal("1"), Decimal("1")), (Decimal("2"), Decimal("2"))),
        ),
    )

    assert persisted.quantile is not None
    assert persisted.quantile.declarations["position_target_policy"] == (
        "explicit_zero_exit_v2"
    )
    assert backtest_repository.create_kwargs is not None
    assert backtest_repository.create_kwargs["parameters"]["symbols"] == ["AAA.SH"]
    assert daily_bar_repository.symbol_requests == [
        ["AAA.SH"],
        ["AAA.SH"],
        ["AAA.SH", "BBB.SH"],
        ["AAA.SH", "BBB.SH"],
    ]
    assert backtest_repository.updated_parameters[-1]["quantile_data_snapshot"][
        "verification_status"
    ] == "verified"
    assert backtest_repository.updated_parameters[-1]["quantile_data_snapshot"][
        "symbols"
    ] == ["AAA.SH", "BBB.SH"]


def test_quantile_corporate_action_failure_does_not_fail_main_run() -> None:
    class QuantileOnlyCorporateActionRepository(FakeCorporateActionRepository):
        def list_for_symbols_date_range(
            self,
            db: object,
            **kwargs: object,
        ) -> list[SimpleNamespace]:
            symbols = set(kwargs["symbols"])
            if "BBB.SH" in symbols:
                return [SimpleNamespace(symbol="BBB.SH", ex_date=date(2026, 7, 2))]
            return []

    backtest_repository = FakeBacktestRepository()
    service = DailyBacktestService(
        daily_bar_repository=FakeDailyBarRepository(),
        backtest_repository=backtest_repository,
        corporate_action_repository=QuantileOnlyCorporateActionRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
    )

    persisted = service.run(
        SimpleNamespace(commit=lambda: None),
        name="quantile-corporate-action",
        strategy_name="factor_selection",
        signals=[TargetWeightSignal(date(2026, 7, 1), "AAA.SH", Decimal("1"))],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
        adjust_type="qfq",
        initial_cash=Decimal("10000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        lot_size=100,
        code_version="abc123",
        data_version="market-qfq-20260702",
        quantile_layer_signals=[
            [TargetWeightSignal(date(2026, 7, 1), "AAA.SH", Decimal("1"))],
            [TargetWeightSignal(date(2026, 7, 1), "BBB.SH", Decimal("1"))],
        ],
        quantile_meta=QuantileBacktestMeta(
            split_factor_name="momentum_20",
            split_factor_version="v1-qfq",
            n_layers=2,
            max_assets_per_layer=None,
            layer_bounds=((Decimal("1"), Decimal("1")), (Decimal("2"), Decimal("2"))),
        ),
    )

    assert persisted.quantile is None
    assert backtest_repository.saved_result is persisted.result
    assert backtest_repository.failed_message is None
    assert "quantile backtest interval contains corporate actions" in (
        backtest_repository.updated_parameters[-1]["quantile_backtest_error"]
    )


def test_quantile_market_change_is_fail_closed_without_failing_main_run() -> None:
    class ChangingQuantileDailyBarRepository(FakeDailyBarRepository):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def list_for_factor_calculation(
            self,
            db: object,
            **kwargs: object,
        ) -> list[SimpleNamespace]:
            self.calls += 1
            rows = [
                SimpleNamespace(
                    symbol=symbol,
                    security_type="stock",
                    trade_date=trade_date,
                    open=Decimal("10"),
                    close=Decimal("10"),
                )
                for trade_date in (date(2026, 7, 1), date(2026, 7, 2))
                for symbol in list(kwargs["symbols"])
            ]
            if self.calls == 4:
                rows[-1].close = Decimal("11")
            return rows

    backtest_repository = FakeBacktestRepository()
    service = DailyBacktestService(
        daily_bar_repository=ChangingQuantileDailyBarRepository(),
        backtest_repository=backtest_repository,
        corporate_action_repository=FakeCorporateActionRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
    )

    persisted = service.run(
        SimpleNamespace(commit=lambda: None),
        name="quantile-market-drift",
        strategy_name="factor_selection",
        signals=[TargetWeightSignal(date(2026, 7, 1), "AAA.SH", Decimal("1"))],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
        adjust_type="qfq",
        initial_cash=Decimal("10000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        lot_size=100,
        code_version="abc123",
        data_version="market-qfq-20260702",
        quantile_layer_signals=[
            [TargetWeightSignal(date(2026, 7, 1), "AAA.SH", Decimal("1"))],
            [TargetWeightSignal(date(2026, 7, 1), "BBB.SH", Decimal("1"))],
        ],
        quantile_meta=QuantileBacktestMeta(
            split_factor_name="momentum_20",
            split_factor_version="v1-qfq",
            n_layers=2,
            max_assets_per_layer=None,
            layer_bounds=((Decimal("1"), Decimal("1")), (Decimal("2"), Decimal("2"))),
        ),
    )

    assert persisted.quantile is None
    assert backtest_repository.saved_result is persisted.result
    assert backtest_repository.failed_message is None
    assert "quantile_backtest_data_changed" in (
        backtest_repository.updated_parameters[-1]["quantile_backtest_error"]
    )
    assert backtest_repository.updated_parameters[-1]["quantile_data_snapshot"][
        "verification_status"
    ] == "changed"


def test_backtest_service_rejects_unhandled_corporate_actions() -> None:
    backtest_repository = FakeBacktestRepository()
    corporate_action_repository = FakeCorporateActionRepository(
        [SimpleNamespace(symbol="AAA.SH", ex_date=date(2026, 7, 2))]
    )
    db = SimpleNamespace(commit=lambda: None)
    service = DailyBacktestService(
        daily_bar_repository=FakeDailyBarRepository(),
        backtest_repository=backtest_repository,
        corporate_action_repository=corporate_action_repository,
        trading_calendar_repository=FakeTradingCalendarRepository(),
    )

    with pytest.raises(
        ValueError,
        match="backtest interval contains corporate actions",
    ):
        service.run(
            db,
            name="corporate-action-guard",
            strategy_name="unit_test",
            signals=[
                TargetWeightSignal(
                    signal_date=date(2026, 7, 1),
                    symbol="aaa.sh",
                    target_weight=Decimal("1"),
                )
            ],
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 2),
            adjust_type="qfq",
            initial_cash=Decimal("10000"),
            commission_rate=Decimal("0"),
            slippage_bps=Decimal("0"),
            lot_size=100,
            code_version="abc123",
            data_version="market-qfq-20260702",
        )

    assert corporate_action_repository.kwargs == {
        "symbols": {"AAA.SH"},
        "start_date": date(2026, 7, 1),
        "end_date": date(2026, 7, 2),
    }
    assert backtest_repository.saved_result is None
    assert backtest_repository.failed_message is not None
    assert "event accounting is not implemented" in backtest_repository.failed_message


def test_backtest_service_fails_when_trusted_market_snapshot_changes() -> None:
    class ChangingDailyBarRepository(FakeDailyBarRepository):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def list_for_factor_calculation(
            self,
            db: object,
            **kwargs: object,
        ) -> list[SimpleNamespace]:
            rows = super().list_for_factor_calculation(db, **kwargs)
            self.calls += 1
            if self.calls == 2:
                rows[-1].close = Decimal("12")
            return rows

    backtest_repository = FakeBacktestRepository()
    service = DailyBacktestService(
        daily_bar_repository=ChangingDailyBarRepository(),
        backtest_repository=backtest_repository,
        corporate_action_repository=FakeCorporateActionRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
    )
    db = SimpleNamespace(commit=lambda: None)

    with pytest.raises(RuntimeError, match="trusted market data changed"):
        service.run(
            db,
            name="data-drift",
            strategy_name="unit_test",
            signals=[
                TargetWeightSignal(
                    signal_date=date(2026, 7, 1),
                    symbol="AAA.SH",
                    target_weight=Decimal("1"),
                )
            ],
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 2),
            adjust_type="none",
            initial_cash=Decimal("10000"),
            commission_rate=Decimal("0"),
            slippage_bps=Decimal("0"),
            lot_size=100,
            code_version="abc123",
            data_version="market-none-20260702",
        )

    assert backtest_repository.saved_result is None
    assert backtest_repository.failed_message is not None
    assert backtest_repository.updated_snapshots[-1]["verification_status"] == "changed"


def test_backtest_service_uses_hk_execution_calendar() -> None:
    calendar_repository = FakeTradingCalendarRepository()
    service = DailyBacktestService(
        daily_bar_repository=FakeDailyBarRepository(symbol="0700.HK"),
        backtest_repository=FakeBacktestRepository(),
        corporate_action_repository=FakeCorporateActionRepository(),
        trading_calendar_repository=calendar_repository,
    )

    service.run(
        object(),
        name="hk-calendar",
        strategy_name="unit_test",
        signals=[
            TargetWeightSignal(
                signal_date=date(2026, 7, 1),
                symbol="0700.HK",
                target_weight=Decimal("1"),
            )
        ],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
        adjust_type="none",
        initial_cash=Decimal("10000"),
        commission_rate=Decimal("0"),
        slippage_bps=Decimal("0"),
        lot_size=100,
        code_version="abc123",
        data_version="market-none-20260702",
    )

    assert calendar_repository.exchanges == ["HK", "HK"]


def test_backtest_service_rejects_symbol_without_known_exchange() -> None:
    backtest_repository = FakeBacktestRepository()
    service = DailyBacktestService(
        daily_bar_repository=FakeDailyBarRepository(symbol="AAA"),
        backtest_repository=backtest_repository,
        corporate_action_repository=FakeCorporateActionRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
    )

    with pytest.raises(ValueError, match="cannot resolve exchange"):
        service.run(
            SimpleNamespace(commit=lambda: None),
            name="unknown-calendar",
            strategy_name="unit_test",
            signals=[
                TargetWeightSignal(
                    signal_date=date(2026, 7, 1),
                    symbol="AAA",
                    target_weight=Decimal("1"),
                )
            ],
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 2),
            adjust_type="none",
            initial_cash=Decimal("10000"),
            commission_rate=Decimal("0"),
            slippage_bps=Decimal("0"),
            lot_size=100,
            code_version="abc123",
            data_version="market-none-20260702",
        )

    assert backtest_repository.saved_result is None
    assert backtest_repository.failed_message is not None


def test_backtest_verification_session_observes_a_new_committed_snapshot(tmp_path) -> None:
    database_path = tmp_path / "verification.duckdb"
    engine = create_engine(f"duckdb:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE snapshot_probe (value INTEGER)"))
        connection.execute(text("INSERT INTO snapshot_probe VALUES (1)"))

    with Session(engine) as db:
        assert db.scalar(text("SELECT value FROM snapshot_probe")) == 1
        with engine.begin() as writer:
            writer.execute(text("UPDATE snapshot_probe SET value = 2"))
        with _independent_verification_session(db) as verification_db:
            assert verification_db is not db
            assert verification_db.scalar(text("SELECT value FROM snapshot_probe")) == 2
