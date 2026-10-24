from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.paper_equity import PaperAccountNotFound
from app.services.position_analysis import PaperPositionAnalysisService

NOW = datetime(2026, 7, 21, tzinfo=UTC)


class FakePaperRepository:
    def __init__(self, *, cash="1000", positions=None) -> None:
        self.account = SimpleNamespace(id=1, name="main", cash_balance=Decimal(cash))
        self.positions = positions or []

    def get_account(self, db, *, account_id: int):
        return self.account if account_id == 1 else None

    def list_positions(self, db, *, account_id: int):
        return [p for p in self.positions if p.quantity != 0]


class FakeSecurityRepository:
    def get_many(self, db, *, symbols: set[str]):
        return [
            SimpleNamespace(
                symbol=symbol,
                name=f"证券{symbol}",
                industry="银行",
                security_type="stock",
                exchange="SH",
            )
            for symbol in sorted(symbols)
        ]


class FakeBarRepository:
    def __init__(self, prices: dict[str, str]) -> None:
        self.prices = {symbol: Decimal(price) for symbol, price in prices.items()}

    def list_latest_consumable_by_symbols(self, db, *, symbols: set[str], adjust_type: str):
        return [
            SimpleNamespace(
                symbol=symbol,
                trade_date=date(2026, 7, 21),
                adjust_type=adjust_type,
                close=self.prices[symbol],
                quality_status="trusted",
            )
            for symbol in sorted(set(symbols))
            if symbol in self.prices
        ]


class FakeEquityRepository:
    def __init__(self, snapshots) -> None:
        self.snapshots = snapshots

    def list_snapshots(self, db, *, account_id: int):
        return self.snapshots


def _position(symbol="600000.SH", quantity=100, cost="10") -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        quantity=quantity,
        average_cost=Decimal(cost),
        updated_at=NOW,
    )


def _snapshot(day: int, equity: str, history_status="continuous") -> SimpleNamespace:
    equity_decimal = Decimal(equity)
    return SimpleNamespace(
        id=day,
        account_id=1,
        valuation_date=date(2026, 7, day),
        cash_balance=equity_decimal,
        positions_market_value=equity_decimal,
        total_equity=equity_decimal,
        high_watermark=equity_decimal,
        drawdown=Decimal("-0.1") if day == 3 else Decimal("0"),
        monitoring_start_date=date(2026, 7, 1),
        observation_count=day,
        history_status=history_status,
        created_at=NOW,
    )


def _service(paper_repository=None, prices=None, snapshots=None):
    return PaperPositionAnalysisService(
        paper_repository=paper_repository or FakePaperRepository(),
        security_repository=FakeSecurityRepository(),
        daily_bar_repository=FakeBarRepository(
            prices if prices is not None else {"600000.SH": "11"}
        ),
        equity_repository=FakeEquityRepository(snapshots or []),
    )


def test_positions_valuation_matches_trusted_price() -> None:
    report = _service(
        paper_repository=FakePaperRepository(cash="1000", positions=[_position()])
    ).get_positions(None, account_id=1)

    assert report.total_equity == Decimal("2100.0000")
    assert report.cash_balance == Decimal("1000.0000")
    holding = report.holdings[0]
    assert holding.market_value == Decimal("1100.0000")
    assert holding.unrealized_pnl == Decimal("100.0000")
    assert holding.unrealized_pnl_pct == Decimal("0.1000000000")
    assert holding.weight == Decimal("0.5238095238")
    assert report.concentration.hhi == (holding.weight * holding.weight).quantize(
        Decimal("0.0000000001")
    )
    assert report.industry_exposure[0].weight == holding.weight
    assert report.price_stale is False


def test_positions_without_trusted_price_marks_stale() -> None:
    report = _service(
        paper_repository=FakePaperRepository(positions=[_position()]),
        prices={},
    ).get_positions(None, account_id=1)

    holding = report.holdings[0]
    assert holding.price_stale is True
    assert holding.market_value == Decimal("1000.0000")
    assert holding.unrealized_pnl is None
    assert report.price_stale is True


def test_positions_unknown_account_raises() -> None:
    with pytest.raises(PaperAccountNotFound):
        _service().get_positions(None, account_id=99)


def test_performance_summarizes_curve_and_daily_volatility() -> None:
    snapshots = [
        _snapshot(1, "10000", "bootstrap"),
        _snapshot(2, "11000"),
        _snapshot(3, "9900"),
    ]
    report = _service(snapshots=snapshots).get_performance(None, account_id=1)

    assert report.account_id == 1
    assert len(report.curve) == 3
    summary = report.summary
    assert summary.observation_count == 3
    assert summary.cumulative_return == Decimal("-0.0100000000")
    assert summary.max_drawdown == Decimal("-0.1000000000")
    # Two days of observation is far too short to annualize.
    assert summary.annualized_return is None
    assert summary.approximate is False
    assert summary.daily_volatility is not None


def test_performance_annualizes_only_after_enough_observation() -> None:
    snapshots = [
        _snapshot(1, "10000", "bootstrap"),
        _snapshot(31, "11000"),
    ]
    summary = _service(snapshots=snapshots).get_performance(None, account_id=1).summary

    assert summary.annualized_return is not None


def test_performance_marks_gap_history_as_approximate() -> None:
    snapshots = [
        _snapshot(1, "10000", "bootstrap"),
        _snapshot(10, "11000", "gap"),
    ]
    summary = _service(snapshots=snapshots).get_performance(None, account_id=1).summary

    assert summary.approximate is True
    assert summary.volatility_approximate is True
    assert summary.daily_volatility is None
