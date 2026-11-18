from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import duckdb
import pytest

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


def test_factor_budget_uses_full_cross_section_without_valuation_queries() -> None:
    class NoValuationQueries:
        def latest_market_cap_by_symbol(self, *args, **kwargs):
            raise AssertionError("valuation styles must be excluded")

        def list_available(self, *args, **kwargs):
            raise AssertionError("valuation styles must be excluded")

    service = PortfolioStyleDataService(
        valuation_repository=NoValuationQueries(),
        factor_repository=FakeFactorRepository(
            rows_by_key={
                ("momentum_20", "v1-qfq"): [
                    _factor_row("A", Decimal("1")),
                    _factor_row("B", Decimal("2")),
                    _factor_row("C", Decimal("3")),
                ]
            }
        ),
    )

    single = service.load_style_exposures(
        db=None,
        as_of_date=date(2026, 7, 21),
        symbols=["A"],
        include_valuation_styles=False,
    )
    subset = service.load_style_exposures(
        db=None,
        as_of_date=date(2026, 7, 21),
        symbols=["A", "B"],
        include_valuation_styles=False,
    )

    assert single == subset
    assert set(single) == {"momentum"}
    assert single["momentum"]["A"] < 0


def test_factor_budget_requires_broad_non_degenerate_provenanced_cross_section() -> None:
    as_of = date(2026, 11, 13)
    symbols = [f"{index:06d}.SZ" for index in range(100)]

    class FullMarket:
        def list_trusted_stock_universe(self, db, *, trade_date):
            assert trade_date == as_of
            return set(symbols)

    class Runs:
        def get_by_id(self, db, run_id):
            return SimpleNamespace(
                id=run_id, status="success", factor_name="momentum_20",
                factor_version="v1-qfq", input_end_date=as_of,
                parameters={"adjust_type": "qfq", "quality_status": "trusted", "symbols": symbols},
            )

    rows = [SimpleNamespace(symbol=symbol, value=Decimal(index), run_id=7)
            for index, symbol in enumerate(symbols)]
    factors = FakeFactorRepository({("momentum_20", "v1-qfq"): rows})
    service = PortfolioStyleDataService(
        factor_repository=factors, factor_run_repository=Runs(),
        daily_bar_repository=FullMarket(),
    )
    evidence = {}
    exposures = service.load_style_exposures(
        db=object(), as_of_date=as_of, symbols=[symbols[0]],
        style_factor_map={"momentum": ("momentum_20", "v1-qfq")},
        include_valuation_styles=False, strict_budget=True, factor_evidence=evidence,
    )
    assert exposures["momentum"][symbols[0]] < 0
    assert evidence == {"momentum": {
        "run_id": 7, "observed_count": 100, "expected_count": 100,
    }}

    factors._rows_by_key[("momentum_20", "v1-qfq")] = rows + [
        SimpleNamespace(symbol="510300.SH", value=Decimal("1000000"), run_id=7)
    ]
    stock_only = service.load_style_exposures(
        db=object(), as_of_date=as_of, symbols=[symbols[0]],
        style_factor_map={"momentum": ("momentum_20", "v1-qfq")},
        include_valuation_styles=False, strict_budget=True,
    )
    assert stock_only == exposures
    with pytest.raises(ValueError, match="trusted stock holdings only"):
        service.load_style_exposures(
            db=object(), as_of_date=as_of, symbols=["510300.SH"],
            style_factor_map={"momentum": ("momentum_20", "v1-qfq")},
            include_valuation_styles=False, strict_budget=True,
        )

    # An ETF row cannot fill a missing stock in the coverage gate.
    factors._rows_by_key[("momentum_20", "v1-qfq")] = rows[:99] + [
        SimpleNamespace(symbol="510300.SH", value=Decimal("1000000"), run_id=7)
    ]
    with pytest.raises(ValueError, match="incomplete or degenerate"):
        service.load_style_exposures(
            db=object(), as_of_date=as_of, symbols=[symbols[0]],
            style_factor_map={"momentum": ("momentum_20", "v1-qfq")},
            include_valuation_styles=False, strict_budget=True,
        )

    factors._rows_by_key[("momentum_20", "v1-qfq")] = rows[:30]
    with pytest.raises(ValueError, match="incomplete or degenerate"):
        service.load_style_exposures(
            db=object(), as_of_date=as_of, symbols=[symbols[0]],
            style_factor_map={"momentum": ("momentum_20", "v1-qfq")},
            include_valuation_styles=False, strict_budget=True,
        )
    factors._rows_by_key[("momentum_20", "v1-qfq")] = [
        SimpleNamespace(symbol=symbol, value=Decimal(1), run_id=7) for symbol in symbols
    ]
    with pytest.raises(ValueError, match="incomplete or degenerate"):
        service.load_style_exposures(
            db=object(), as_of_date=as_of, symbols=[symbols[0]],
            style_factor_map={"momentum": ("momentum_20", "v1-qfq")},
            include_valuation_styles=False, strict_budget=True,
        )


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


def test_risk_prices_keep_dates_and_read_curated_qfq_once(tmp_path, monkeypatch) -> None:
    connection = duckdb.connect(":memory:")
    try:
        path = (tmp_path / "prices.parquet").as_posix()
        connection.execute(
            f"COPY (SELECT * FROM (VALUES "
            "('A.SH', DATE '2026-11-02', 10.0::DECIMAL(12,2)), "
            "('A.SH', DATE '2026-11-04', 11.0::DECIMAL(12,2)), "
            "('BM.SH', DATE '2026-11-02', 100.0::DECIMAL(12,2)), "
            "('BM.SH', DATE '2026-11-04', 101.0::DECIMAL(12,2))"
            ") AS t(symbol, trade_date, close)) "
            f"TO '{path}' (FORMAT PARQUET)"
        )
    finally:
        connection.close()

    class FakeWarmStore:
        def prices_qfq_glob(self):
            return tmp_path

    service = PortfolioStyleDataService(warm_store=FakeWarmStore())
    prices, benchmark = service.load_risk_prices(
        None, symbols=["A.SH"], benchmark_symbol="BM.SH", end_date=date(2026, 11, 4)
    )

    assert prices == {
        "A.SH": {date(2026, 11, 2): Decimal("10"), date(2026, 11, 4): Decimal("11")}
    }
    assert benchmark == {
        date(2026, 11, 2): Decimal("100"), date(2026, 11, 4): Decimal("101")
    }

    from app.services.release import ReleaseService

    expected_checksum = [ReleaseService.compute_parquet_checksum(tmp_path)]

    class FakeReleaseRepository:
        def get_active(self, db, *, dataset):
            assert dataset == "curated.prices_qfq"
            return SimpleNamespace(
                release_id="test-release",
                quality_status="trusted",
                max_date=date(2026, 11, 4),
                checksum=expected_checksum[0],
            )

    monkeypatch.setattr(
        "app.services.portfolio_style_data.ReleaseRepository", FakeReleaseRepository
    )
    expected_checksum[0] = "wrong-checksum"
    with pytest.raises(ValueError, match="differ from active release checksum"):
        service.load_risk_prices(
            object(), symbols=["A.SH"], benchmark_symbol="BM.SH",
            end_date=date(2026, 11, 4),
        )
    expected_checksum[0] = ReleaseService.compute_parquet_checksum(tmp_path)

    class FakeCalendar:
        def list_open_dates(self, db, *, exchange, start_date, end_date):
            return [date(2026, 11, day) for day in (2, 3, 4)]

    monkeypatch.setattr("app.services.portfolio_style_data.TradingCalendarRepository", FakeCalendar)
    with pytest.raises(ValueError, match="open-day gap for A.SH"):
        service.load_risk_prices(
            object(), symbols=["A.SH"], benchmark_symbol="BM.SH",
            end_date=date(2026, 11, 4),
        )
