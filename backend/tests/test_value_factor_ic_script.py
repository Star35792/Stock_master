"""Correctness tests for the value-factor IC script.

The script is the only consumer of ``research.valuation_history``.  Three
properties are asserted here because breaking any of them would silently
produce plausible-looking but wrong evidence:

* the **financial sector must be excluded** -- banks, insurers and brokers
  are structurally cheap, so leaving 122 of them in the cross-section lets
  the sector dominate the "cheapest" quantile and turns the factor into a
  sector bet;
* ``value_pe_ttm`` must stay retired -- ``-PE_TTM`` maps loss-making
  companies to large positive numbers;
* the coverage denominator must be the **price** universe, not the factor's
  own non-null set.
"""

from __future__ import annotations

import json
from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import duckdb
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.models.market import Security
from app.repositories.warm_layer import WarmLayerStore


def _load_script():
    script_path = (
        Path(__file__).parents[1] / "scripts" / "factors" / "compute_value_factor_ic.py"
    )
    spec = spec_from_file_location("compute_value_factor_ic_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPT = _load_script()

#: Six trading days, enough for a 5-day forward return to exist.
TRADE_DATES = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7),
               date(2026, 1, 8), date(2026, 1, 9), date(2026, 1, 12)]


@pytest.fixture
def security_session():
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA IF NOT EXISTS market"))
    Base.metadata.create_all(engine, tables=[Security.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.execute(
            text(
                "INSERT INTO market.security "
                "(id, symbol, name, security_type, exchange, industry) VALUES "
                "(1, '000001.SZ', '平安银行', 'stock', 'SZ', '银行'), "
                "(2, '600036.SH', '招商银行', 'stock', 'SH', '银行'), "
                "(3, '600519.SH', '贵州茅台', 'stock', 'SH', '白酒'), "
                "(4, '601318.SH', '中国平安', 'stock', 'SH', '保险'), "
                "(5, '000300.SH', '沪深300', 'index', 'SH', '指数')"
            )
        )
        db.commit()
        yield db


def test_financial_industry_symbols_excludes_banks_and_insurers(
    security_session,
) -> None:
    excluded = SCRIPT.financial_industry_symbols(security_session)
    assert excluded == ["000001.SZ", "600036.SH", "601318.SH"]


def test_value_pe_ttm_is_retired() -> None:
    assert "value_pe_ttm" not in SCRIPT.FACTOR_SQL
    assert set(SCRIPT.FACTOR_SQL) == {"value_earnings_yield", "value_pb_mrq"}
    # The earnings yield only exists for a positive PE.
    assert "pe_ttm > 0" in SCRIPT.FACTOR_SQL["value_earnings_yield"]


def test_pit_status_marks_pb_as_look_ahead() -> None:
    assert SCRIPT.PIT_STATUS["value_earnings_yield"] == "point_in_time"
    assert SCRIPT.PIT_STATUS["value_pb_mrq"] == "look_ahead"


def _write_valuation_release(root: Path) -> None:
    release = root / "exported" / "20260113-0000"
    target = release / "research" / "valuation_history"
    target.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE v (symbol VARCHAR, trade_date DATE, pe_ttm DOUBLE, pb_mrq DOUBLE)"
    )
    rows = []
    for offset in range(60):
        symbol = f"{offset:06d}.SZ"
        # A deterministic cross-section: the value rises with the index, so
        # the top quantile is a well-defined bucket.
        pe = 5.0 + offset * 0.5
        pb = 1.0 + offset * 0.1
        for trade_date in TRADE_DATES:
            rows.append((symbol, trade_date, pe, pb))
    con.executemany("INSERT INTO v VALUES (?, ?, ?, ?)", rows)
    con.execute(
        "COPY (SELECT * FROM v ORDER BY symbol, trade_date) "
        f"TO '{target.as_posix()}/data_0.parquet' (FORMAT PARQUET)"
    )
    con.close()
    (release / "export_summary.json").write_text(
        json.dumps({"release_tag": "20260113-0000", "all_ok": True}),
        encoding="utf-8",
    )


def _write_prices(root: Path) -> str:
    curated = root / "curated" / "prices_qfq"
    curated.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE p (symbol VARCHAR, trade_date DATE, close DOUBLE)")
    rows = []
    for offset in range(60):
        symbol = f"{offset:06d}.SZ"
        for trade_date in TRADE_DATES:
            rows.append((symbol, trade_date, 10.0 + offset))
    con.executemany("INSERT INTO p VALUES (?, ?, ?)", rows)
    con.execute(
        "COPY (SELECT * FROM p ORDER BY symbol, trade_date) "
        f"TO '{curated.as_posix()}/data_0.parquet' (FORMAT PARQUET)"
    )
    con.close()
    return f"{curated.as_posix()}/**/*.parquet"


def test_load_daily_ic_excludes_the_financial_sector(tmp_path: Path) -> None:
    """The excluded symbols must not enter the cross-section at all."""
    warm_root = tmp_path / "warm"
    (warm_root / "exported").mkdir(parents=True)
    _write_valuation_release(warm_root)
    prices_glob = _write_prices(tmp_path)

    store = WarmLayerStore(warm_root)
    con = store.connect(release_tag="20260113-0000", attach_control=False)
    try:
        rows = SCRIPT._load_daily_ic(
            con,
            prices_glob,
            TRADE_DATES[0],
            TRADE_DATES[-1],
            tuple(SCRIPT.FACTOR_SQL),
            ("000000.SZ",),
        )
    finally:
        con.close()

    assert rows, "the fixture must produce at least one cross-section"
    for row in rows:
        assert row["observation_count"] == 59
        assert row["universe_size"] == 60


def test_load_daily_ic_computes_endpoint_spreads(tmp_path: Path) -> None:
    """The endpoint spreads the pool classifier gates on must be present."""
    warm_root = tmp_path / "warm"
    (warm_root / "exported").mkdir(parents=True)
    _write_valuation_release(warm_root)
    prices_glob = _write_prices(tmp_path)

    store = WarmLayerStore(warm_root)
    con = store.connect(release_tag="20260113-0000", attach_control=False)
    try:
        rows = SCRIPT._load_daily_ic(
            con,
            prices_glob,
            TRADE_DATES[0],
            TRADE_DATES[-1],
            tuple(SCRIPT.FACTOR_SQL),
        )
    finally:
        con.close()

    assert rows
    sample = rows[0]
    for key in (
        "top_q_1d",
        "bottom_q_1d",
        "top_q_5d",
        "bottom_q_5d",
        "top_q_20d",
        "bottom_q_20d",
        "monotonicity_1d",
        "top_quantile_turnover",
    ):
        assert key in sample, key
    assert sample["top_q_1d"] is not None
    assert sample["bottom_q_1d"] is not None
    # The coverage denominator is the price universe, not the factor's own
    # non-null set, so both counters refer to the price cross-section.
    assert sample["universe_size"] == 60
    assert sample["observation_count"] == 60
