from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.backtest import TargetWeightSignal
from app.services import DailyBacktestService


class FakeDailyBarRepository:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

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
                symbol="AAA",
                security_type="stock",
                trade_date=date(2026, 7, 1),
                open=first_open,
                close=first_close,
            ),
            SimpleNamespace(
                symbol="AAA",
                security_type="stock",
                trade_date=date(2026, 7, 2),
                open=second_open,
                close=second_close,
            ),
        ]


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

    def create_run(self, db: object, **kwargs: object) -> SimpleNamespace:
        self.create_kwargs = kwargs
        return SimpleNamespace(id=1)

    def save_result(self, db: object, *, run: object, result: object) -> None:
        self.saved_result = result

    def fail_run(self, db: object, *, run: object, error_message: str) -> None:
        self.failed_message = error_message


def test_backtest_service_reads_trusted_market_and_persists_result() -> None:
    daily_bar_repository = FakeDailyBarRepository()
    backtest_repository = FakeBacktestRepository()
    service = DailyBacktestService(
        daily_bar_repository=daily_bar_repository,
        backtest_repository=backtest_repository,
        corporate_action_repository=FakeCorporateActionRepository(),
    )

    persisted = service.run(
        object(),
        name="smoke",
        strategy_name="unit_test",
        signals=[
            TargetWeightSignal(
                signal_date=date(2026, 7, 1),
                symbol="aaa",
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
    assert daily_bar_repository.kwargs["symbols"] == ["AAA"]
    assert daily_bar_repository.kwargs["quality_status"] == "trusted"
    assert backtest_repository.create_kwargs is not None
    assert backtest_repository.create_kwargs["parameters"]["signals"][0]["symbol"] == "AAA"
    assert backtest_repository.create_kwargs["parameters"]["adjust_type"] == "none"
    assert (
        backtest_repository.create_kwargs["parameters"]["corporate_action_policy"]
        == "reject"
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
    )

    persisted = service.run(
        object(),
        name="contract",
        strategy_name="factor_selection",
        signals=[
            TargetWeightSignal(
                signal_date=date(2026, 7, 1),
                symbol="aaa",
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


def test_backtest_service_rejects_unhandled_corporate_actions() -> None:
    backtest_repository = FakeBacktestRepository()
    corporate_action_repository = FakeCorporateActionRepository(
        [SimpleNamespace(symbol="AAA", ex_date=date(2026, 7, 2))]
    )
    db = SimpleNamespace(commit=lambda: None)
    service = DailyBacktestService(
        daily_bar_repository=FakeDailyBarRepository(),
        backtest_repository=backtest_repository,
        corporate_action_repository=corporate_action_repository,
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
                    symbol="aaa",
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
        "symbols": {"AAA"},
        "start_date": date(2026, 7, 1),
        "end_date": date(2026, 7, 2),
    }
    assert backtest_repository.saved_result is None
    assert backtest_repository.failed_message is not None
    assert "event accounting is not implemented" in backtest_repository.failed_message
