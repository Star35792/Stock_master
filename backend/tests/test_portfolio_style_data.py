from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.portfolio_style_data import PortfolioStyleDataService


class FakeValuationRepository:
    def __init__(self, market_caps=None, valuation_rows=None):
        self._market_caps = market_caps or {}
        self._valuation_rows = valuation_rows or []

    def latest_market_cap_by_symbol(self, db, *, trade_date, symbols):
        return {s: self._market_caps[s] for s in symbols if s in self._market_caps}

    def list_available(self, db, *, start_date, end_date, symbols):
        return [row for row in self._valuation_rows if row.symbol in symbols]


class FakeFactorRepository:
    def __init__(self, rows_by_key=None):
        self._rows_by_key = rows_by_key or {}

    def list_for_cross_section(self, db, *, factor_name, factor_version, start_date, end_date):
        key = (factor_name, factor_version)
        return self._rows_by_key.get(key, [])


def _val_row(symbol, pb):
    return SimpleNamespace(symbol=symbol, pb=pb)


def _factor_row(symbol, value):
    return SimpleNamespace(symbol=symbol, value=value)


def test_z_score_returns_zero_for_single_symbol() -> None:
    result = PortfolioStyleDataService._z_score({"A": Decimal("5")})
    assert result["A"] == Decimal("0")


def test_z_score_standardizes_multiple_symbols() -> None:
    result = PortfolioStyleDataService._z_score(
        {"A": Decimal("1"), "B": Decimal("2"), "C": Decimal("3")}
    )
    # mean=2, sample std ~1.225, A=-0.816, B=0, C=0.816
    assert result["A"] < result["B"] < result["C"]
    assert result["B"] == Decimal("0")


def test_load_style_exposures_combines_size_value_and_factor_styles() -> None:
    service = PortfolioStyleDataService(
        valuation_repository=FakeValuationRepository(
            market_caps={
                "A": Decimal("1000000"),
                "B": Decimal("2000000"),
            },
            valuation_rows=[
                _val_row("A", Decimal("2")),
                _val_row("B", Decimal("4")),
            ],
        ),
        factor_repository=FakeFactorRepository(
            rows_by_key={
                ("momentum_20", "v1-qfq"): [
                    _factor_row("A", Decimal("1.5")),
                    _factor_row("B", Decimal("0.5")),
                ],
            }
        ),
    )

    result = service.load_style_exposures(
        db=None,
        as_of_date=date(2026, 7, 21),
        symbols=["A", "B"],
    )

    assert set(result.keys()) == {"size", "value", "momentum"}
    assert set(result["size"].keys()) == {"A", "B"}
    assert result["value"]["A"] > result["value"]["B"]  # -PB: A=-2 > B=-4
    assert result["momentum"]["A"] > result["momentum"]["B"]


def test_load_style_exposures_respects_custom_style_factor_map() -> None:
    service = PortfolioStyleDataService(
        valuation_repository=FakeValuationRepository(),
        factor_repository=FakeFactorRepository(
            rows_by_key={
                ("custom_factor", "v1"): [
                    _factor_row("A", Decimal("10")),
                    _factor_row("B", Decimal("20")),
                ],
            }
        ),
    )

    result = service.load_style_exposures(
        db=None,
        as_of_date=date(2026, 7, 21),
        symbols=["A", "B"],
        style_factor_map={"custom": ("custom_factor", "v1")},
    )

    assert "custom" in result
    assert "momentum" not in result


def test_load_style_exposures_skips_missing_market_cap() -> None:
    service = PortfolioStyleDataService(
        valuation_repository=FakeValuationRepository(
            market_caps={"A": Decimal("1000000")}
        ),
        factor_repository=FakeFactorRepository(),
    )

    result = service.load_style_exposures(
        db=None,
        as_of_date=date(2026, 7, 21),
        symbols=["A", "B"],
    )

    assert result["size"].keys() == {"A"}


def test_shift_trading_days_doubles_window_to_avoid_calendar_gaps() -> None:
    service = PortfolioStyleDataService()
    end = date(2026, 7, 21)
    result = service._shift_trading_days(None, end, window_days=10)
    assert result == end - __import__("datetime").timedelta(days=20)
