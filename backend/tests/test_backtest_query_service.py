from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import BacktestQueryService


class FakeBacktestRepository:
    def __init__(self) -> None:
        self.trade_kwargs: dict | None = None
        self.equity_batch_calls: list[list[int]] = []

    def list_equity_values_by_run_ids(self, db: object, *, run_ids: list[int]):
        self.equity_batch_calls.append(run_ids)
        return {1: [Decimal("100000"), Decimal("110000")]} if 1 in run_ids else {}

    def list_equity_dates_by_run_ids(self, db: object, *, run_ids: list[int]):
        return {run_id: (date(2026, 7, 1), date(2026, 7, 2)) for run_id in run_ids}

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
    def list_sharpe_by_run_ids(
        self, db: object, *, run_ids: list[int]
    ) -> dict[int, tuple[Decimal | None, str]]:
        return {}

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
    repository = FakeBacktestRepository()
    page = BacktestQueryService(
        backtest_repository=repository,
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
    assert page.items[0].performance_status == "derived"
    assert page.items[0].sharpe_ratio is not None
    assert repository.equity_batch_calls == [[1]]


def test_backtest_list_uses_persisted_sharpe_without_recalculation() -> None:
    class PersistedSharpe(FakePerformanceRepository):
        def list_sharpe_by_run_ids(self, db: object, *, run_ids: list[int]):
            assert run_ids == [1]
            return {1: (Decimal("1.25"), "analysis-v2")}

    repository = FakeBacktestRepository()
    page = BacktestQueryService(
        backtest_repository=repository,
        performance_repository=PersistedSharpe(),
    ).list_runs(object(), limit=20, offset=0)

    assert page.items[0].performance_status == "analyzed"
    assert page.items[0].sharpe_ratio == Decimal("1.25")
    assert page.items[0].performance_code_version == "analysis-v2"
    assert repository.equity_batch_calls == [[]]


def test_backtest_preview_preserves_order_contract_and_metric_source() -> None:
    class PreviewRuns(FakeBacktestRepository):
        def get_run(self, db: object, *, run_id: int):
            base = self.list_runs(db)[0][0]
            return SimpleNamespace(
                **{
                    **vars(base),
                    "id": run_id,
                    "data_version": "market-v2" if run_id == 2 else "market-v1",
                    "parameters": {
                        "validation_mode": "out_of_sample" if run_id == 2 else "exploratory",
                        "fee_model_version": "a_share_cost_v2",
                        "commission_rate": "0.0003",
                    },
                }
            )

    class PreviewPerformance(FakePerformanceRepository):
        def get_by_run_id(self, db: object, *, run_id: int):
            if run_id == 1:
                return SimpleNamespace(
                    total_return=Decimal("0.02"),
                    annualized_return=Decimal("0.2"),
                    maximum_drawdown=Decimal("-0.04"),
                    sharpe_ratio=Decimal("1.5"),
                    turnover_rate=Decimal("0.3"),
                    total_commission=Decimal("12"),
                    code_version="saved-v1",
                )
            return None

        def load_inputs(self, db: object, *, run_id: int):
            run = PreviewRuns().get_run(db, run_id=run_id)
            if run_id == 3:
                return run, [], [], []
            return run, [
                SimpleNamespace(trade_date=date(2026, 7, 1), total_equity=Decimal("100000")),
                SimpleNamespace(trade_date=date(2026, 7, 2), total_equity=Decimal("101000")),
            ], [], []

    items = BacktestQueryService(
        backtest_repository=PreviewRuns(), performance_repository=PreviewPerformance()
    ).compare_preview(object(), run_ids=[2, 1, 3])
    assert [item.run_id for item in items] == [2, 1, 3]
    assert [item.metric_source for item in items] == ["derived", "persisted", "missing_equity"]
    assert items[0].data_version == "market-v2"
    assert items[0].validation_mode == "out_of_sample"
    assert items[0].fee_parameters["commission_rate"] == "0.0003"
    assert items[0].sharpe_ratio is not None
    assert items[0].delta_status == "baseline"
    assert items[1].delta_status == "unavailable"
    assert "data_version" in items[1].delta_reasons
    assert items[1].metric_code_version == "saved-v1"
    assert items[2].sharpe_ratio is None


def test_backtest_preview_delta_requires_aligned_observations_and_metric_version() -> None:
    fee_contract = {
        "fee_model_version": "a_share_cost_v2",
        "commission_rate": "0.0003",
        "min_commission": "5",
        "sell_stamp_duty_rate": "0.0005",
        "transfer_fee_rate": "0.00001",
        "exchange_fee_rate": "0.0000487",
        "slippage_bps": "2",
        "security_type_fee_rule": "stock_or_etf_v1",
        "stock_stamp_duty_rule": "legal_20230828_v1",
        "supported_trade_date_start": "2022-04-29",
        "validation_mode": "rolling",
        "adjust_type": "qfq",
        "factor_specs": [{"factor_name": "momentum_20", "higher_is_better": True}],
        "symbols": ["AAA", "BBB"],
        "signals": [{"trade_date": "2026-07-01", "symbol": "AAA"}],
        "strategy_input_snapshot": {"sha256": "input-v1", "row_count": 100},
        "data_snapshot": {"sha256": "market-v1", "bar_count": 100},
    }

    class Runs(FakeBacktestRepository):
        candidate_parameters = {
            **fee_contract,
            "factor_specs": [{"factor_name": "momentum_20", "higher_is_better": False}],
            "symbols": ["BBB", "AAA"],
            "data_snapshot": {"sha256": "market-v2", "bar_count": 100},
        }
        candidate_dates = (date(2026, 7, 1), date(2026, 7, 2))
        candidate_training_artifact_id = 10

        def get_run(self, db: object, *, run_id: int):
            base = self.list_runs(db)[0][0]
            return SimpleNamespace(**{
                **vars(base), "id": run_id,
                "training_artifact_id": 10 if run_id == 1 else self.candidate_training_artifact_id,
                "parameters": fee_contract if run_id == 1 else self.candidate_parameters,
            })

        def list_equity_dates_by_run_ids(self, db: object, *, run_ids: list[int]):
            assert run_ids == [1, 2]
            return {1: (date(2026, 7, 1), date(2026, 7, 2)),
                    2: self.candidate_dates}

    class Performance(FakePerformanceRepository):
        candidate_version = "analysis-v1"
        candidate_sharpe = Decimal("1.2")

        def get_by_run_id(self, db: object, *, run_id: int):
            return SimpleNamespace(
                total_return=Decimal("0.10") if run_id == 1 else Decimal("0.13"),
                annualized_return=Decimal("0.2"),
                maximum_drawdown=Decimal("-0.04") if run_id == 1 else Decimal("-0.06"),
                sharpe_ratio=Decimal("1.5") if run_id == 1 else self.candidate_sharpe,
                turnover_rate=Decimal("0.3"), total_commission=Decimal("12"),
                code_version="analysis-v1" if run_id == 1 else self.candidate_version,
            )

    runs = Runs()
    performance = Performance()

    def preview():
        return BacktestQueryService(
            backtest_repository=runs, performance_repository=performance,
        ).compare_preview(object(), run_ids=[1, 2])[1]

    aligned = preview()
    assert aligned.delta_status == "available"
    assert aligned.total_return_delta == Decimal("0.03")
    assert aligned.maximum_drawdown_delta == Decimal("-0.02")
    assert aligned.sharpe_ratio_delta == Decimal("-0.3")
    assert aligned.strategy_differences == ["factor_specs", "data_snapshot_sha256"]
    assert aligned.strategy_contract["factor_specs"][0]["higher_is_better"] is False
    assert aligned.strategy_contract["strategy_input_sha256"] == "input-v1"
    assert aligned.strategy_contract["data_snapshot_sha256"] == "market-v2"
    assert aligned.strategy_contract["training_artifact_id"] == 10
    assert "signals" not in aligned.strategy_contract
    assert "strategy_input_snapshot" not in aligned.strategy_contract

    runs.candidate_training_artifact_id = 11
    training_change = preview()
    assert "training_artifact_id" in training_change.strategy_differences
    runs.candidate_training_artifact_id = 10

    runs.candidate_parameters = {**runs.candidate_parameters, "symbols": ["BBB", "CCC"]}
    universe_change = preview()
    assert "symbols" in universe_change.strategy_differences
    runs.candidate_parameters = {**runs.candidate_parameters, "symbols": ["BBB", "AAA"]}

    performance.candidate_sharpe = None
    no_sharpe = preview()
    assert no_sharpe.delta_status == "available"
    assert no_sharpe.total_return_delta == Decimal("0.03")
    assert no_sharpe.sharpe_ratio_delta is None

    performance.candidate_version = "analysis-v2"
    version_mismatch = preview()
    assert version_mismatch.delta_status == "unavailable"
    assert "metric_code_version" in version_mismatch.delta_reasons
    assert version_mismatch.total_return_delta is None

    performance.candidate_version = "analysis-v1"
    runs.candidate_dates = (date(2026, 7, 1), date(2026, 7, 3))
    date_mismatch = preview()
    assert "equity_dates_differ" in date_mismatch.delta_reasons
    assert date_mismatch.sharpe_ratio_delta is None

    runs.candidate_dates = ()
    missing_dates = preview()
    assert "equity_dates_missing" in missing_dates.delta_reasons

    runs.candidate_dates = (date(2026, 7, 1), date(2026, 7, 2))
    runs.candidate_parameters = {**fee_contract, "stock_stamp_duty_rule": "fixed_rate_v1"}
    fee_mismatch = preview()
    assert "fee_parameters" in fee_mismatch.delta_reasons


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
