from datetime import date
from decimal import Decimal
from types import SimpleNamespace

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
                    symbol="AAA",
                    value=Decimal("1"),
                ),
                SimpleNamespace(
                    trade_date=date(2026, 7, 1),
                    symbol="BBB",
                    value=Decimal("2"),
                ),
            ]
        return [
            SimpleNamespace(
                trade_date=date(2026, 7, 1),
                symbol="AAA",
                value=Decimal("3"),
            ),
            SimpleNamespace(
                trade_date=date(2026, 7, 1),
                symbol="BBB",
                value=Decimal("1"),
            ),
        ]


class FakeSecurityRepository:
    def get_security_types(self, db: object, symbols: set[str]) -> dict[str, str]:
        return {symbol: "stock" for symbol in symbols}


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
        factor_specs=[
            FactorScoreSpec("momentum_20", "v1-qfq", weight=Decimal("0.6")),
            FactorScoreSpec("quality_20", "v1-qfq", weight=Decimal("0.4")),
        ],
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

    assert result.run_id == 7
    assert backtest_service.kwargs is not None
    assert backtest_service.kwargs["strategy_name"] == "factor_selection"
    assert backtest_service.kwargs["name"] == "factor-selection-smoke"
    assert backtest_service.kwargs["initial_cash"] == Decimal("100000")
    assert factor_value_repository.requests[0]["factor_name"] == "momentum_20"
    assert factor_value_repository.requests[1]["factor_name"] == "quality_20"
