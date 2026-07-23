from datetime import date

import duckdb
import pytest

from app.lake import CuratedDailyBarReader, CuratedLayer, DataLake


def _seed_price_lake(tmp_path, *, symbol: str = "000001.SZ") -> DataLake:
    lake = DataLake(tmp_path / "lake")
    daily = lake.root / "daily_bar" / "release=test"
    actions = lake.root / "corporate_action" / "release=test"
    daily.mkdir(parents=True)
    actions.mkdir(parents=True)
    sym = symbol.replace("'", "''")
    connection = duckdb.connect()
    try:
        connection.execute(
            f"""
            COPY (
                SELECT '{sym}' symbol, trade_date,
                       close AS open, close AS high, close AS low, close,
                       100::BIGINT volume, 1000::DECIMAL(24,4) amount
                FROM (VALUES
                    ('2026-01-05'::DATE, 10::DECIMAL(18,4)),
                    ('2026-01-06'::DATE, 9::DECIMAL(18,4)),
                    ('2026-01-07'::DATE, 9.9::DECIMAL(18,4))
                ) bars(trade_date, close)
            ) TO ? (FORMAT PARQUET)
            """,
            [str(daily / "data.parquet")],
        )
        connection.execute(
            f"COPY (SELECT '{sym}' symbol, '2026-01-06'::DATE ex_date, "
            "0::DECIMAL(18,8) dividend_per_share, 0::DECIMAL(18,8) per_share_bonus, "
            "0::DECIMAL(18,8) allotment_ratio, 0::DECIMAL(18,8) allotment_price) "
            "TO ? (FORMAT PARQUET)",
            [str(actions / "data.parquet")],
        )
    finally:
        connection.close()
    lake._write_manifest(  # noqa: SLF001
        [
            {
                "dataset": dataset,
                "release": "test",
                "path": str(path),
                "source_path": "test",
                "source_sha256": "test",
                "row_count": count,
                "min_date": "2026-01-05",
                "max_date": "2026-01-07",
                "created_at": "2026-01-07T00:00:00+00:00",
            }
            for dataset, path, count in (
                ("daily_bar", daily, 3),
                ("corporate_action", actions, 1),
            )
        ]
    )
    return lake


def _reader(tmp_path, **kwargs) -> CuratedDailyBarReader:
    lake = _seed_price_lake(tmp_path, **kwargs)
    CuratedLayer(lake).build_prices_qfq()
    return CuratedDailyBarReader(lake)


def test_reader_returns_factor_attribute_surface(tmp_path) -> None:
    reader = _reader(tmp_path)

    bars = reader.list_for_factor_calculation(
        None,
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 7),
        adjust_type="qfq",
    )

    assert [b.trade_date for b in bars] == [
        date(2026, 1, 5),
        date(2026, 1, 6),
        date(2026, 1, 7),
    ]
    first = bars[0]
    # Every attribute factor services read must be present and typed.
    assert first.symbol == "000001.SZ"
    assert first.security_type == "stock"
    assert first.close is not None
    assert first.volume is not None
    assert first.amount is not None
    # turnover_rate is not in the 10y dump; served as NULL, services null-guard it.
    assert first.turnover_rate is None


def test_reader_derives_index_security_type(tmp_path) -> None:
    reader = _reader(tmp_path, symbol="000300.SH")

    bars = reader.list_for_factor_calculation(
        None, start_date=date(2026, 1, 5), end_date=date(2026, 1, 7), adjust_type="qfq"
    )

    assert {b.security_type for b in bars} == {"index"}


def test_reader_filters_by_symbol_and_date(tmp_path) -> None:
    reader = _reader(tmp_path)

    bars = reader.list_for_factor_calculation(
        None,
        start_date=date(2026, 1, 6),
        end_date=date(2026, 1, 6),
        adjust_type="qfq",
        symbols=["000001.sz"],  # lower-case must be upper-normalized
    )

    assert len(bars) == 1
    assert bars[0].trade_date == date(2026, 1, 6)


def test_reader_counts_trusted_universe_by_date(tmp_path) -> None:
    reader = _reader(tmp_path)

    counts = reader.count_trusted_by_date(
        None, start_date=date(2026, 1, 5), end_date=date(2026, 1, 7), adjust_type="qfq"
    )

    assert counts == {date(2026, 1, 5): 1, date(2026, 1, 6): 1, date(2026, 1, 7): 1}


def test_reader_rejects_unsupported_adjust_type(tmp_path) -> None:
    reader = _reader(tmp_path)

    with pytest.raises(ValueError, match="qfq"):
        reader.list_for_factor_calculation(
            None,
            start_date=date(2026, 1, 5),
            end_date=date(2026, 1, 7),
            adjust_type="hfq",
        )


def test_reader_errors_when_curated_missing(tmp_path) -> None:
    lake = _seed_price_lake(tmp_path)
    reader = CuratedDailyBarReader(lake)  # never built

    with pytest.raises(LookupError, match="prices_qfq"):
        reader.list_for_factor_calculation(
            None, start_date=date(2026, 1, 5), end_date=date(2026, 1, 7), adjust_type="qfq"
        )
