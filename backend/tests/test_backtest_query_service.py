from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services import BacktestQueryService


class FakeBacktestRepository:
    def __init__(self) -> None:
        self.trade_kwargs: dict | None = None

    def list_runs(self, db: object, **kwargs: object) -> tuple[list[SimpleNamespace], int]:
        return [
            SimpleNamespace(
                id=1,
                name="factor-smoke",
                strategy_name="factor_selection",
                parameters={
                    "strategy_name": "factor_selection",
                    "factor_specs": [
                        {"factor_name": "momentum_20", "factor_version": "v1-qfq"}
                    ],
                    "symbols": ["AAA"],
                },
                code_version="abc123",
                data_version="market-qfq-20260720",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 20),
                initial_cash=Decimal("100000"),
                final_equity=Decimal("110000"),
                total_return=Decimal("0.1"),
                status="success",
                started_at=datetime(2026, 7, 20, tzinfo=UTC),
                finished_at=datetime(2026, 7, 20, tzinfo=UTC),
                error_message=None,
                created_at=datetime(2026, 7, 20, tzinfo=UTC),
            )
        ], 1

    def get_run(self, db: object, *, run_id: int) -> SimpleNamespace | None:
        return None

    def list_trades(self, db: object, **kwargs: object) -> tuple[list[SimpleNamespace], int]:
        self.trade_kwargs = kwargs
        return [
            SimpleNamespace(
                id=1,
                run_id=1,
                signal_date=date(2026, 7, 1),
                trade_date=date(2026, 7, 2),
                symbol="AAA",
                side="buy",
                quantity=100,
                reference_price=Decimal("10"),
                fill_price=Decimal("10.01"),
                commission=Decimal("0.3"),
                cash_after=Decimal("99000"),
                created_at=datetime(2026, 7, 20, tzinfo=UTC),
            )
        ], 1


class FakePerformanceRepository:
    def get_by_run_id(self, db: object, *, run_id: int) -> None:
        return None


class ComparisonPerformanceRepository(FakePerformanceRepository):
    def get_by_run_id(self, db: object, *, run_id: int):
        return SimpleNamespace(
            total_return=Decimal("0.20") if run_id == 1 else Decimal("0.10"),
            annualized_return=Decimal("0.20"),
            maximum_drawdown=Decimal("-0.05"),
            sharpe_ratio=Decimal("1.5"),
            turnover_rate=Decimal("0.3"),
        )


class FakeDailyBarRepository:
    def list_by_symbol(self, db: object, **kwargs: object):
        return [
            SimpleNamespace(trade_date=date(2026, 7, 1), close=Decimal("100")),
            SimpleNamespace(trade_date=date(2026, 7, 2), close=Decimal("105")),
        ], 2


def test_backtest_query_service_lists_runs() -> None:
    page = BacktestQueryService(
        backtest_repository=FakeBacktestRepository(),
        performance_repository=FakePerformanceRepository(),
    ).list_runs(
        object(),
        strategy_name="factor_selection",
        status="success",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 20),
        limit=100,
        offset=0,
    )

    assert page.total == 1
    assert page.items[0].name == "factor-smoke"
    assert page.items[0].parameters["factor_specs"][0]["factor_version"] == "v1-qfq"


def test_backtest_query_service_normalizes_trade_symbol_filter() -> None:
    repository = FakeBacktestRepository()
    page = BacktestQueryService(
        backtest_repository=repository,
        performance_repository=FakePerformanceRepository(),
    ).list_trades(
        object(),
        run_id=1,
        symbol="aaa",
        start_date=None,
        end_date=None,
        limit=100,
        offset=0,
    )

    assert page.total == 1
    assert page.items[0].symbol == "AAA"
    assert repository.trade_kwargs is not None
    assert repository.trade_kwargs["symbol"] == "AAA"


def test_backtest_query_service_calculates_benchmark_excess_return() -> None:
    repository = FakeBacktestRepository()
    repository.get_run = lambda db, *, run_id: SimpleNamespace(
        id=run_id,
        status="success",
        parameters={"benchmark": "000300.sh", "adjust_type": "qfq"},
        total_return=Decimal("0.10"),
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
    )
    result = BacktestQueryService(
        backtest_repository=repository,
        performance_repository=FakePerformanceRepository(),
        daily_bar_repository=FakeDailyBarRepository(),
    ).get_benchmark_comparison(object(), run_id=1)

    assert result.benchmark == "000300.SH"
    assert result.benchmark_total_return == Decimal("0.05")
    assert result.excess_return == Decimal("0.05")


def test_backtest_query_service_compares_successful_runs() -> None:
    repository = FakeBacktestRepository()
    repository.get_run = lambda db, *, run_id: SimpleNamespace(
        id=run_id,
        name=f"run-{run_id}",
        strategy_name="factor_selection",
        status="success",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
    )
    result = BacktestQueryService(
        backtest_repository=repository,
        performance_repository=ComparisonPerformanceRepository(),
    ).compare_runs(object(), run_ids=[2, 1])

    assert [item.run_id for item in result] == [1, 2]
