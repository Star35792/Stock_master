from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

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

    def list_equity(self, db: object, **kwargs: object):
        return [
            SimpleNamespace(trade_date=date(2026, 7, 1), total_equity=Decimal("100")),
            SimpleNamespace(trade_date=date(2026, 7, 2), total_equity=Decimal("110")),
        ], 2

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
    def __init__(
        self,
        metrics_by_run_id: dict[int, tuple[Decimal, Decimal]] | None = None,
    ) -> None:
        self.metrics_by_run_id = metrics_by_run_id or {
            1: (Decimal("0.20"), Decimal("-0.05")),
            2: (Decimal("0.10"), Decimal("-0.05")),
        }

    def get_by_run_id(self, db: object, *, run_id: int):
        total_return, maximum_drawdown = self.metrics_by_run_id[run_id]
        return SimpleNamespace(
            total_return=total_return,
            annualized_return=Decimal("0.20"),
            maximum_drawdown=maximum_drawdown,
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


def test_benchmark_comparison_uses_run_snapshot_after_market_changes() -> None:
    repository = FakeBacktestRepository()
    repository.get_run = lambda db, *, run_id: SimpleNamespace(
        id=run_id, status="success",
        parameters={
            "benchmark": "000300.SH",
            "benchmark_snapshot": {
                "status": "verified", "start_date": "2026-07-01",
                "end_date": "2026-07-02", "date_count": 2,
                "first_close": "100", "last_close": "105",
            },
        },
    )

    class ChangedMarket:
        def list_by_symbol(self, db, **kwargs):
            raise AssertionError("snapshot query must not read current market")

    result = BacktestQueryService(
        backtest_repository=repository,
        performance_repository=FakePerformanceRepository(),
        daily_bar_repository=ChangedMarket(),
    ).get_benchmark_comparison(object(), run_id=1)
    assert result.input_source == "run_snapshot"
    assert result.benchmark_total_return == Decimal("0.05")


@pytest.mark.parametrize("available_days", [
    (date(2026, 7, 2), date(2026, 7, 3)),
    (date(2026, 7, 1), date(2026, 7, 3)),
    (date(2026, 7, 1), date(2026, 7, 2)),
])
def test_benchmark_rejects_missing_equity_date(available_days) -> None:
    repository = FakeBacktestRepository()
    repository.get_run = lambda db, *, run_id: SimpleNamespace(
        id=run_id, status="success", parameters={"benchmark": "000300.SH"},
    )
    repository.list_equity = lambda db, **kwargs: ([
        SimpleNamespace(trade_date=date(2026, 7, 1), total_equity=Decimal("100")),
        SimpleNamespace(trade_date=date(2026, 7, 2), total_equity=Decimal("105")),
        SimpleNamespace(trade_date=date(2026, 7, 3), total_equity=Decimal("110")),
    ], 3)
    class PartialDailyBars:
        def list_by_symbol(self, db, **kwargs):
            return [
                SimpleNamespace(trade_date=day, close=Decimal("100"))
                for day in available_days
            ], len(available_days)

    with pytest.raises(ValueError, match="benchmark dates do not cover"):
        BacktestQueryService(
            backtest_repository=repository,
            performance_repository=FakePerformanceRepository(),
            daily_bar_repository=PartialDailyBars(),
        ).get_benchmark_comparison(object(), run_id=1)


def test_benchmark_requires_configuration() -> None:
    repository = FakeBacktestRepository()
    repository.get_run = lambda db, *, run_id: SimpleNamespace(
        id=run_id, status="success", parameters={},
    )
    with pytest.raises(ValueError, match="benchmark is not configured"):
        BacktestQueryService(
            backtest_repository=repository,
            performance_repository=FakePerformanceRepository(),
        ).get_benchmark_comparison(object(), run_id=1)


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


def test_backtest_query_service_detects_thresholded_drift() -> None:
    repository = FakeBacktestRepository()
    repository.get_run = lambda db, *, run_id: SimpleNamespace(
        id=run_id,
        name=f"run-{run_id}",
        strategy_name="factor_selection",
        status="success",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
        parameters={"top_n": 10, "code_version": f"code-{run_id}"},
        data_version="market-qfq-20260720",
    )
    service = BacktestQueryService(
        backtest_repository=repository,
        performance_repository=ComparisonPerformanceRepository(),
    )

    within_tolerance = service.detect_drift(
        object(),
        baseline_run_id=1,
        candidate_run_ids=[2],
        return_tolerance=Decimal("0.10"),
        drawdown_tolerance=Decimal("0"),
    )
    above_tolerance = service.detect_drift(
        object(),
        baseline_run_id=1,
        candidate_run_ids=[2],
        return_tolerance=Decimal("0.09"),
        drawdown_tolerance=Decimal("0"),
    )

    assert within_tolerance.return_spread == Decimal("0.10")
    assert within_tolerance.drawdown_spread == Decimal("0.00")
    assert within_tolerance.drift_detected is False
    assert above_tolerance.drift_detected is True


def test_backtest_query_service_uses_baseline_relative_drift_spread() -> None:
    repository = FakeBacktestRepository()
    repository.get_run = lambda db, *, run_id: SimpleNamespace(
        id=run_id,
        name=f"run-{run_id}",
        strategy_name="factor_selection",
        status="success",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
        parameters={"top_n": 10, "code_version": f"code-{run_id}"},
        data_version="market-qfq-20260720",
    )
    service = BacktestQueryService(
        backtest_repository=repository,
        performance_repository=ComparisonPerformanceRepository(
            {
                1: (Decimal("0.20"), Decimal("-0.10")),
                2: (Decimal("0.25"), Decimal("-0.13")),
                3: (Decimal("0.15"), Decimal("-0.07")),
            }
        ),
    )

    result = service.detect_drift(
        object(),
        baseline_run_id=1,
        candidate_run_ids=[2, 3],
        return_tolerance=Decimal("0.05"),
        drawdown_tolerance=Decimal("0.03"),
    )

    assert result.return_spread == Decimal("0.05")
    assert result.drawdown_spread == Decimal("0.03")
    assert result.drift_detected is False


def test_backtest_query_service_rejects_incomparable_drift_runs() -> None:
    repository = FakeBacktestRepository()
    repository.get_run = lambda db, *, run_id: SimpleNamespace(
        id=run_id,
        name=f"run-{run_id}",
        strategy_name="factor_selection",
        status="success",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 2),
        parameters={"top_n": 10, "code_version": f"code-{run_id}"},
        data_version=f"market-qfq-v{run_id}",
    )
    service = BacktestQueryService(
        backtest_repository=repository,
        performance_repository=ComparisonPerformanceRepository(),
    )

    try:
        service.detect_drift(
            object(),
            baseline_run_id=1,
            candidate_run_ids=[2],
            return_tolerance=Decimal(0),
            drawdown_tolerance=Decimal(0),
        )
    except ValueError as exc:
        assert "not comparable with baseline 1: 2" in str(exc)
    else:
        raise AssertionError("runs with different data versions must be rejected")


def test_final_holdings_reads_last_day_and_computes_weights(monkeypatch) -> None:
    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace

    from app.services.backtest import BacktestQueryService

    service = BacktestQueryService()
    run = SimpleNamespace(
        id=14,
        status="success",
        end_date=date(2026, 4, 30),
        final_equity=Decimal("1120000"),
    )
    equity = SimpleNamespace(
        trade_date=date(2026, 4, 30),
        cash=Decimal("62000"),
        total_equity=Decimal("1120000"),
    )
    positions = [
        SimpleNamespace(
            symbol="AAA.SH",
            quantity=2000,
            close=Decimal("10"),
            market_value=Decimal("20000"),
        ),
        SimpleNamespace(
            symbol="BBB.SZ",
            quantity=1000,
            close=Decimal("20"),
            market_value=Decimal("20000"),
        ),
    ]
    monkeypatch.setattr(
        service.backtest_repository, "get_run", lambda db, *, run_id: run
    )
    monkeypatch.setattr(
        service.backtest_repository,
        "latest_equity",
        lambda db, *, run_id: equity,
    )
    monkeypatch.setattr(
        service.backtest_repository,
        "latest_positions",
        lambda db, *, run_id: positions,
    )

    class FakeSecurityRepository:
        def get_many(self, db, *, symbols):
            return [
                SimpleNamespace(
                    symbol=symbol,
                    name=f"证券{symbol}",
                    industry="银行",
                    security_type="stock",
                )
                for symbol in sorted(symbols)
            ]

    report = service.get_final_holdings(
        object(),
        run_id=14,
        research_db=object(),
        security_repository=FakeSecurityRepository(),
    )

    assert report is not None
    assert report.as_of_date == date(2026, 4, 30)
    assert report.cash == Decimal("62000")
    assert report.total_equity == Decimal("1120000")
    assert [row.symbol for row in report.holdings] == ["AAA.SH", "BBB.SZ"]
    assert report.holdings[0].weight == Decimal("0.0178571429")
    assert report.holdings[0].industry == "银行"


def test_final_holdings_rejects_failed_run() -> None:
    import pytest

    from app.services.backtest import BacktestQueryService

    service = BacktestQueryService()
    monkey_run = SimpleNamespace(id=1, status="failed")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        service.backtest_repository, "get_run", lambda db, *, run_id: monkey_run
    )
    with pytest.raises(ValueError, match="successful"):
        service.get_final_holdings(object(), run_id=1)
    monkeypatch.undo()
