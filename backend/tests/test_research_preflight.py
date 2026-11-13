from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.schemas.strategy import FactorSelectionBacktestRequest
from app.services.research_preflight import check_factor_selection
from app.services.strategy_research import StrategyResearchService


def _request(**changes):
    values = {
        "name": "preview", "factor_specs": [{"factor_name": "momentum_20", "factor_version": "v1"}],
        "top_n": 10, "start_date": date(2026, 7, 1), "end_date": date(2026, 7, 3),
        "initial_cash": "100000", "code_version": "test", "data_version": "test",
    }
    values.update(changes)
    return FactorSelectionBacktestRequest.model_validate(values)


def test_preflight_rejects_unsupported_mode_before_reading_data():
    report = check_factor_selection(
        object(), payload=_request(validation_mode="rolling"),
        service=object(), portfolio_constraints=None,
    )
    assert report["runnable_now"] is False
    assert report["evidence_qualified"] is False
    assert report["checks"][0]["code"] == "validation"
    assert len(report["input_sha256"]) == 64


def test_preflight_reports_missing_factor_without_creating_run():
    class MissingFactorService:
        def _normalize_factor_specs(self, specs):
            return specs

        def _load_factor_rows(self, *args):
            raise ValueError("factor values not found")

    report = check_factor_selection(
        object(), payload=_request(),
        service=MissingFactorService(), portfolio_constraints=None,
    )
    assert report["runnable_now"] is False
    assert report["checks"][0]["code"] == "run_blocker"
    assert "factor values not found" in report["checks"][0]["detail"]


def test_preflight_uses_real_daily_engine_without_persisting():
    days = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]

    class Factors:
        def list_for_cross_section(self, db, **kwargs):
            return [
                SimpleNamespace(trade_date=days[0], symbol="AAA.SH", value=Decimal("2"), run_id=1),
                SimpleNamespace(trade_date=days[0], symbol="BBB.SH", value=Decimal("1"), run_id=1),
            ]

    class Securities:
        def get_security_types(self, db, symbols):
            return {"AAA.SH": "stock", "BBB.SH": "stock"}

    class Reader:
        def list_corporate_actions(self, **kwargs):
            assert kwargs["symbols"] == {"AAA.SH"}
            return []

        def list_daily_bars(self, **kwargs):
            symbols = kwargs["symbols"]
            assert symbols == {"AAA.SH"}
            return [SimpleNamespace(
                symbol=symbol, trade_date=day, open=Decimal("10"), close=Decimal("10"),
            ) for symbol in symbols for day in days]

        def load_execution_calendar(self, **kwargs):
            return {symbol: days for symbol in kwargs["symbols"]}

    class Backtest:
        research_read_source = "control"
        research_release_tag = None
        research_min_trading_days = 0

        @contextmanager
        def _research_read(self, db, **kwargs):
            yield Reader(), "test_read_only"

    service = StrategyResearchService(
        factor_value_repository=Factors(), security_repository=Securities(),
        backtest_service=Backtest(),
    )
    report = check_factor_selection(
        object(), payload=_request(top_n=1), service=service, portfolio_constraints=None,
    )
    assert report["runnable_now"] is True
    assert report["evidence_qualified"] is False
    assert any(
        item["code"] == "execution" and item["status"] == "passed"
        for item in report["checks"]
    )
