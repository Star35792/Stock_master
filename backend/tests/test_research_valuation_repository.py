"""Regression tests for the ORM twin of the valuation-history read path.

The backend-layer rules make the ORM repository and the warm repository
**twins**: the same query semantics must exist on both paths, with identical
column names, ordering and filters.  These tests pin the ORM side and then
check the two against each other, so a change to one that is not mirrored in
the other fails here.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database.session import initialize_database
from app.models.market.valuation_snapshot import ValuationSnapshot
from app.repositories.research_read import WarmResearchReadRepository
from app.repositories.research_valuation import ResearchValuationReadRepository
from app.repositories.warm_layer import WarmLayerStore

_TRADE_DATES = (date(2026, 1, 5), date(2026, 1, 6))

#: The columns both paths must expose, in the same order.
_SHARED_COLUMNS: tuple[str, ...] = (
    "symbol",
    "trade_date",
    "close",
    "pe_ttm",
    "pb_mrq",
    "ps_ttm",
    "total_market_cap",
    "total_shares",
    "free_shares_a",
    "float_market_cap",
    "change_rate",
    "pit_verified",
)


@pytest.fixture
def db_session(tmp_path) -> Session:
    """Provide a session on a throw-away control-plane database."""
    engine = create_engine(f"duckdb:///{tmp_path / 'valuation.duckdb'}")
    initialize_database(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def _seed_control_plane(db: Session) -> None:
    """Insert the control-plane valuation snapshots."""
    db.add_all(
        [
            ValuationSnapshot(
                symbol="000001.SZ",
                trade_date=date(2026, 1, 5),
                close=Decimal("10.1234"),
                pe_ttm=Decimal("12.5"),
                pb=Decimal("1.5"),
                ps_ttm=Decimal("2.5"),
                total_market_cap=Decimal("1000000"),
                float_market_cap=Decimal("900000"),
                total_shares=Decimal("100000"),
                float_shares=Decimal("80000"),
                currency="CNY",
                source_name="eastmoney",
            ),
            ValuationSnapshot(
                symbol="000002.SZ",
                trade_date=date(2026, 1, 5),
                close=Decimal("20.5000"),
                pe_ttm=Decimal("8.0"),
                pb=Decimal("0.9"),
                ps_ttm=Decimal("1.1"),
                total_market_cap=Decimal("2000000"),
                float_market_cap=Decimal("1800000"),
                total_shares=Decimal("200000"),
                float_shares=Decimal("150000"),
                currency="CNY",
                source_name="eastmoney",
            ),
        ]
    )
    db.commit()


def _write_warm_release(root: Path) -> None:
    """Publish a release carrying the same two rows as the control plane."""
    release = root / "exported" / "twin"
    target = release / "research" / "valuation_history"
    target.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    try:
        con.execute(
            """
            CREATE TABLE valuation_history (
                symbol VARCHAR, trade_date DATE, close DECIMAL(18,4),
                pe_ttm DECIMAL(24,10), pb_mrq DECIMAL(24,10),
                ps_ttm DECIMAL(24,10), total_market_cap DECIMAL(30,6),
                total_shares DECIMAL(30,6), free_shares_a DECIMAL(30,6),
                float_market_cap DECIMAL(30,6), change_rate DECIMAL(18,8),
                pit_verified BOOLEAN, source_name VARCHAR, observed_at VARCHAR
            )
            """
        )
        con.execute(
            """
            INSERT INTO valuation_history VALUES
                ('000001.SZ', DATE '2026-01-05', 10.1234, 12.5, 1.5, 2.5,
                 1000000, 100000, 80000, 900000, 1.25, FALSE,
                 'eastmoney', '2026-01-06T00:00:00+00:00'),
                ('000002.SZ', DATE '2026-01-05', 20.5000, 8.0, 0.9, 1.1,
                 2000000, 200000, 150000, 1800000, -0.50, FALSE,
                 'eastmoney', '2026-01-06T00:00:00+00:00')
            """
        )
        con.execute(
            "COPY (SELECT * FROM valuation_history ORDER BY trade_date, symbol) "
            f"TO '{target.as_posix()}/data_0.parquet' (FORMAT PARQUET)"
        )
    finally:
        con.close()
    (release / "export_summary.json").write_text(
        json.dumps({"release_tag": "twin", "all_ok": True}), encoding="utf-8"
    )


def test_orm_reads_rows_in_trade_date_symbol_order(db_session: Session) -> None:
    _seed_control_plane(db_session)
    repository = ResearchValuationReadRepository(db_session)
    rows = repository.list_valuation_history(
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
    )
    assert [row.symbol for row in rows] == ["000001.SZ", "000002.SZ"]
    assert rows[0].trade_date == date(2026, 1, 5)
    assert rows[0].close == Decimal("10.1234")
    assert rows[0].pe_ttm == Decimal("12.5")
    assert rows[0].total_market_cap == Decimal("1000000")
    assert rows[0].free_shares_a == Decimal("80000")


def test_orm_filters_by_symbol(db_session: Session) -> None:
    _seed_control_plane(db_session)
    repository = ResearchValuationReadRepository(db_session)
    rows = repository.list_valuation_history(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 31),
        symbols=["000002.SZ"],
    )
    assert [row.symbol for row in rows] == ["000002.SZ"]


def test_orm_coverage_reports_the_window(db_session: Session) -> None:
    _seed_control_plane(db_session)
    repository = ResearchValuationReadRepository(db_session)
    coverage = repository.valuation_history_coverage(
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
    )
    assert coverage["row_count"] == 2
    assert coverage["trading_days"] == 1
    assert coverage["symbol_count"] == 2
    assert coverage["min_date"] == date(2026, 1, 5)


def test_orm_coverage_is_empty_without_rows(db_session: Session) -> None:
    repository = ResearchValuationReadRepository(db_session)
    coverage = repository.valuation_history_coverage(
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
    )
    assert coverage["row_count"] == 0
    assert coverage["trading_days"] == 0


def test_orm_and_warm_expose_the_same_columns(
    db_session: Session, tmp_path: Path
) -> None:
    """The two twins must expose the same attributes."""
    _seed_control_plane(db_session)
    _write_warm_release(tmp_path)
    store = WarmLayerStore(tmp_path)
    con = store.connect(release_tag="twin", read_only=True, attach_control=False)
    try:
        warm_rows = WarmResearchReadRepository(con).list_valuation_history(
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
        )
    finally:
        con.close()
    orm_rows = ResearchValuationReadRepository(db_session).list_valuation_history(
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
    )
    for warm, orm in zip(warm_rows, orm_rows, strict=True):
        assert vars(warm).keys() == vars(orm).keys()
        for column in _SHARED_COLUMNS:
            assert hasattr(warm, column)
            assert hasattr(orm, column)
        assert warm.symbol == orm.symbol
        assert warm.trade_date == orm.trade_date
        assert warm.close == orm.close
        assert warm.pe_ttm == orm.pe_ttm
        assert warm.ps_ttm == orm.ps_ttm
        assert warm.total_market_cap == orm.total_market_cap
        assert warm.total_shares == orm.total_shares
        assert warm.free_shares_a == orm.free_shares_a
        assert warm.float_market_cap == orm.float_market_cap


def test_orm_reports_pb_mrq_as_unavailable(db_session: Session) -> None:
    """The control plane never recorded the PB switching rule.

    The warm layer's ``PB_MRQ`` leaks the balance sheet by up to 120 days, so
    the twin must not present the control plane's ``pb`` as the same field.
    """
    _seed_control_plane(db_session)
    repository = ResearchValuationReadRepository(db_session)
    rows = repository.list_valuation_history(
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
    )
    assert all(row.pb_mrq is None for row in rows)


def test_orm_marks_live_snapshots_as_point_in_time(db_session: Session) -> None:
    """The control plane rows were captured live, so they are PIT observations."""
    _seed_control_plane(db_session)
    repository = ResearchValuationReadRepository(db_session)
    rows = repository.list_valuation_history(
        start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
    )
    assert all(row.pit_verified is True for row in rows)
