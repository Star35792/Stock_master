"""Regression tests for warm-layer IC tie handling."""

import math
from datetime import date
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import duckdb
import pytest

from app.factors.profile_evidence import parse_profile_evidence_identity
from app.factors.quantile_diagnostics import prepare_tie_aware_quantiles


def _load_warm_ic_script():
    script_path = (
        Path(__file__).parents[1] / "scripts" / "factors" / "compute_warm_factor_ic.py"
    )
    spec = spec_from_file_location("compute_warm_factor_ic_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows_for_day(
    trade_date: date,
    groups: list[tuple[Decimal, list[str]]],
) -> list[tuple[str, date, str, Decimal, Decimal, Decimal, Decimal]]:
    rows = []
    for value, symbols in groups:
        for offset, symbol in enumerate(symbols, start=1):
            forward_return = Decimal(offset) / Decimal("100")
            rows.append(
                (
                    "factor_a",
                    trade_date,
                    symbol,
                    value,
                    forward_return,
                    forward_return,
                    forward_return,
                )
            )
    return rows


def test_tie_aware_quantiles_use_average_ranks_and_complete_groups() -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE joined (
            factor_name VARCHAR,
            trade_date DATE,
            symbol VARCHAR,
            value DECIMAL(24, 10),
            fwd1 DECIMAL(24, 10),
            fwd5 DECIMAL(24, 10),
            fwd20 DECIMAL(24, 10)
        )
        """
    )
    first = date(2026, 1, 5)
    second = date(2026, 1, 6)
    third = date(2026, 1, 7)
    rows = _rows_for_day(
        first,
        [
            (Decimal("0"), ["L1", "L2"]),
            (Decimal("1"), ["M1", "M2", "M3", "M4", "M5"]),
            (Decimal("2"), ["H1", "H2", "H3"]),
        ],
    )
    rows += _rows_for_day(
        second,
        [
            (Decimal("0"), ["L1", "L2"]),
            (Decimal("1"), ["M1", "M2", "M3"]),
            (Decimal("2"), ["H2", "H3", "H4", "H5", "H6"]),
        ],
    )
    rows += _rows_for_day(
        third,
        [
            (Decimal("0"), ["L1", "L2"]),
            (Decimal("1"), ["M1", "M2", "M3", "M4", "M5"]),
            (Decimal("2"), ["H1", "H2", "H3"]),
        ],
    )
    con.executemany("INSERT INTO joined VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    prepare_tie_aware_quantiles(con)

    first_groups = con.execute(
        """
        SELECT value, min(rk_value_1d), max(rk_value_1d), min(bucket), max(bucket), count(*)
        FROM scored
        WHERE trade_date = ?
        GROUP BY value
        ORDER BY value
        """,
        [first],
    ).fetchall()
    assert first_groups == [
        (Decimal("0E-10"), 1.5, 1.5, 1, 1, 2),
        (Decimal("1.0000000000"), 5.0, 5.0, 3, 3, 5),
        (Decimal("2.0000000000"), 9.0, 9.0, 5, 5, 3),
    ]
    endpoint_counts = con.execute(
        """
        SELECT
            count(*) FILTER (WHERE value <= bottom_boundary_1d),
            count(*) FILTER (WHERE value >= top_boundary_1d)
        FROM scored
        WHERE trade_date = ?
        """,
        [first],
    ).fetchone()
    assert endpoint_counts == (2, 3)

    turnover = con.execute(
        """
        SELECT trade_date, top_quantile_turnover
        FROM top_turnover
        WHERE trade_date IN (?, ?)
        ORDER BY trade_date
        """,
        [second, third],
    ).fetchall()
    # {H1,H2,H3} -> {H2,H3,H4,H5,H6}: 1 - 2*2/(3+5) = 0.5.
    assert turnover[0][1] == pytest.approx(0.5)
    # Contracting the same tie group uses the same symmetric denominator.
    assert turnover[1][1] == pytest.approx(0.5)
    con.close()


def test_symbol_renaming_and_input_order_do_not_change_tie_diagnostics() -> None:
    def diagnostics(prefix: str, reverse: bool) -> tuple:
        con = duckdb.connect(":memory:")
        con.execute(
            """
            CREATE TABLE joined (
                factor_name VARCHAR, trade_date DATE, symbol VARCHAR,
                value DECIMAL(24, 10), fwd1 DECIMAL(24, 10),
                fwd5 DECIMAL(24, 10), fwd20 DECIMAL(24, 10)
            )
            """
        )
        groups = [
            (Decimal("0"), [f"{prefix}L1", f"{prefix}L2"]),
            (Decimal("1"), [f"{prefix}M{i}" for i in range(1, 6)]),
            (Decimal("2"), [f"{prefix}H{i}" for i in range(1, 4)]),
        ]
        rows = _rows_for_day(date(2026, 1, 5), groups)
        con.executemany(
            "INSERT INTO joined VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(reversed(rows)) if reverse else rows,
        )
        prepare_tie_aware_quantiles(con)
        result = con.execute(
            """
            SELECT
                avg(fwd1) FILTER (WHERE value <= bottom_boundary_1d),
                avg(fwd1) FILTER (WHERE value >= top_boundary_1d),
                list(DISTINCT bucket ORDER BY bucket),
                list(DISTINCT rk_value_1d ORDER BY rk_value_1d)
            FROM scored
            """
        ).fetchone()
        con.close()
        return result

    assert diagnostics("A", False) == diagnostics("Z", True)


def test_zero_dispersion_has_neutral_endpoints_and_no_monotonicity() -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE joined AS
        SELECT
            'factor_a'::VARCHAR AS factor_name,
            DATE '2026-01-05' AS trade_date,
            'S' || range::VARCHAR AS symbol,
            1::DECIMAL(24, 10) AS value,
            range::DECIMAL(24, 10) / 100 AS fwd1,
            range::DECIMAL(24, 10) / 100 AS fwd5,
            range::DECIMAL(24, 10) / 100 AS fwd20
        FROM range(10)
        """
    )

    prepare_tie_aware_quantiles(con)

    row = con.execute(
        """
        SELECT
            min(rk_value_1d),
            max(rk_value_1d),
            min(bucket),
            max(bucket),
            avg(fwd1) FILTER (WHERE value >= top_boundary_1d),
            avg(fwd1) FILTER (WHERE value <= bottom_boundary_1d)
        FROM scored
        """
    ).fetchone()
    assert row[:4] == (5.5, 5.5, 3, 3)
    assert row[4] == row[5]
    monotonicity = con.execute(
        "SELECT corr(bucket, bucket_mean_fwd1) FROM bucket_returns"
    ).fetchone()[0]
    assert monotonicity is None or math.isnan(monotonicity)
    con.close()


def test_missing_forward_return_does_not_receive_a_rank() -> None:
    con = duckdb.connect(":memory:")
    con.execute(
        """
        CREATE TABLE joined (
            factor_name VARCHAR,
            trade_date DATE,
            symbol VARCHAR,
            value DECIMAL(24, 10),
            fwd1 DECIMAL(24, 10),
            fwd5 DECIMAL(24, 10),
            fwd20 DECIMAL(24, 10)
        )
        """
    )
    con.execute(
        """
        INSERT INTO joined VALUES
            ('factor_a', DATE '2026-01-05', 'A', 1, 0.01, NULL, NULL),
            ('factor_a', DATE '2026-01-05', 'B', 2, 0.02, 0.03, NULL)
        """
    )

    prepare_tie_aware_quantiles(con)

    assert con.execute(
        "SELECT rk_value_5d, rk_fwd5, rk_value_20d, rk_fwd20 "
        "FROM scored WHERE symbol = 'A'"
    ).fetchone() == (None, None, None, None)
    assert con.execute(
        "SELECT rk_value_5d, rk_fwd5 FROM scored WHERE symbol = 'B'"
    ).fetchone() == (1.0, 1.0)
    con.close()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source_table": "joined; DROP TABLE joined"}, "source_table"),
        ({"quantile_count": 1}, "quantile_count"),
        ({"endpoint_share": Decimal("0")}, "endpoint_share"),
        ({"endpoint_share": Decimal("0.6")}, "endpoint_share"),
    ],
)
def test_invalid_quantile_parameters_fail_closed(kwargs, message: str) -> None:
    con = duckdb.connect(":memory:")
    with pytest.raises(ValueError, match=message):
        prepare_tie_aware_quantiles(con, **kwargs)
    con.close()


def test_warm_ic_script_includes_complete_endpoint_ties(tmp_path: Path) -> None:
    script = _load_warm_ic_script()
    first = date(2026, 1, 5)
    second = date(2026, 1, 6)
    con = duckdb.connect(":memory:")
    con.execute("CREATE SCHEMA factor")
    con.execute(
        """
        CREATE TABLE factor.factor_value (
            symbol VARCHAR,
            trade_date DATE,
            factor_name VARCHAR,
            factor_version VARCHAR,
            value DECIMAL(24, 10)
        )
        """
    )
    factor_rows = []
    price_rows = []
    for offset in range(50):
        symbol = f"{offset:06d}.SZ"
        value = 0 if offset < 10 else 1 if offset < 35 else 2
        factor_rows.extend(
            [
                (symbol, first, "factor_a", "v-test", value),
                (symbol, second, "factor_a", "v-test", value),
            ]
        )
        price_rows.extend(
            [
                (symbol, first, Decimal("100")),
                (symbol, second, Decimal("100") * (1 + Decimal(offset) / 1000)),
            ]
        )
    con.executemany("INSERT INTO factor.factor_value VALUES (?, ?, ?, ?, ?)", factor_rows)
    con.execute(
        "CREATE TABLE prices (symbol VARCHAR, trade_date DATE, close DECIMAL(24, 10))"
    )
    con.executemany("INSERT INTO prices VALUES (?, ?, ?)", price_rows)
    parquet_path = tmp_path / "prices.parquet"
    con.execute(
        f"COPY prices TO '{parquet_path.as_posix()}' (FORMAT PARQUET)"
    )

    rows = script._load_daily_ic(con, parquet_path.as_posix(), first, second, "v-test")

    assert len(rows) == 1
    # Nominal top size is 10, but all 15 securities tied at value=2 belong to
    # the endpoint. Their average return is mean(35..49)/1000 = 0.042.
    assert rows[0]["top_q_1d"] == pytest.approx(0.042)
    assert rows[0]["bottom_q_1d"] == pytest.approx(0.0045)
    con.close()


def test_warm_ic_evidence_identity_requires_complete_price_dates() -> None:
    script = _load_warm_ic_script()
    first = date(2026, 1, 5)
    second = date(2026, 1, 6)
    daily = [
        {"factor_name": "complete", "trade_date": first},
        {"factor_name": "complete", "trade_date": second},
        {"factor_name": "missing", "trade_date": first},
    ]

    identities = script._build_evidence_identities(
        daily,
        expected_trade_dates=[first, second],
        requested_start_date=first,
        requested_end_date=second,
        release_tag="release-a",
        factor_version="v-test",
        code_version="source-a",
        factor_snapshot_sha256="f" * 64,
        price_snapshot_sha256="e" * 64,
    )

    assert set(identities) == {"complete"}
    identity = parse_profile_evidence_identity(identities["complete"])
    assert identity is not None
    assert identity.trade_date_count == 2


def test_warm_ic_source_snapshot_rejects_price_release_mismatch(tmp_path: Path) -> None:
    script = _load_warm_ic_script()
    warm_root = tmp_path / "warm"
    factor_root = warm_root / "exported" / "release-a" / "factor" / "factor_value"
    factor_root.mkdir(parents=True)
    (factor_root / "part.parquet").write_bytes(b"factor")
    prices = tmp_path / "prices_qfq"
    prices.mkdir()
    (prices / "part.parquet").write_bytes(b"prices")

    class Store:
        def __init__(self) -> None:
            self.warm_root = warm_root

        def table_globs(self, *args):
            return [factor_root]

    with pytest.raises(ValueError, match="does not match"):
        script._capture_source_snapshots(
            Store(),
            release_tag="release-a",
            curated_prices=prices,
            expected_price_checksum="0" * 64,
        )
