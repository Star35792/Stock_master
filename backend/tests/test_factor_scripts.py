"""Correctness tests for the warm-layer factor backfill SQL.

The synthetic fixture mirrors the curated ``prices_qfq`` schema closely enough
for the SQL pipeline (``base`` temp table construction lives in ``main()``, so
the tests recreate it verbatim) and compares every new factor against a pure
Python mirror of the registered ORM semantics in
``app/services/basic_price_volume_factors.py``.
"""

import math
from datetime import date, timedelta
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import duckdb
import pytest


def _load_backfill_module():
    script_path = (
        Path(__file__).parents[1] / "scripts" / "factors" / "backfill_warm_factors.py"
    )
    spec = spec_from_file_location("backfill_warm_factors_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_batch_profiles_defaults_to_market_cap_v1() -> None:
    script_path = Path(__file__).parents[1] / "scripts" / "factors" / "run_batch_profiles.py"
    spec = spec_from_file_location("run_batch_profiles_script", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    args = module.build_parser().parse_args(
        ["--start-date", "2026-07-01", "--end-date", "2026-07-17"]
    )

    assert args.size_factor_name == "market_cap"
    assert args.size_factor_version == "v1"


def test_factor_pool_cli_exports_horizon_observation_counts() -> None:
    script_path = (
        Path(__file__).parents[1] / "scripts" / "factors" / "classify_factor_pool.py"
    )
    source = script_path.read_text(encoding="utf-8")

    for field_name in (
        "rank_ic_1d_observation_count",
        "rank_ic_5d_observation_count",
        "rank_ic_20d_observation_count",
        "endpoint_observation_count",
        "evidence_eligibility_count",
        "evidence_identity_count",
        "evidence_identity_valid",
        "evidence_identity_release_tag",
        "trade_date_sha256",
    ):
        assert f'"{field_name}"' in source


def _synthetic_closes(symbol_index: int, day_index: int) -> Decimal:
    base = Decimal("10") + Decimal(symbol_index) * Decimal("7.5")
    drift = Decimal("0.11") * day_index
    wobble = Decimal(day_index % 13) * Decimal("0.37")
    return base + drift + wobble


def _synthetic_volume(symbol_index: int, day_index: int) -> Decimal:
    return Decimal(1_000_000) + Decimal(symbol_index) * Decimal(50_000) + Decimal(
        (day_index * 7 + symbol_index * 3) % 11
    ) * Decimal(1_000)


@pytest.fixture()
def backfill_env(tmp_path: Path):
    module = _load_backfill_module()
    start = date(2024, 1, 1)
    days = [start + timedelta(days=i) for i in range(70)]
    symbols = [
        ("600000.SH", "stock", 0),
        ("000001.SZ", "stock", 1),
        ("000300.SH", "index", 2),
    ]

    rows = []
    for symbol, security_type, symbol_index in symbols:
        for day_index, trade_date in enumerate(days):
            rows.append(
                (
                    symbol,
                    security_type,
                    trade_date,
                    _synthetic_closes(symbol_index, day_index),
                    _synthetic_volume(symbol_index, day_index),
                )
            )

    prices_dir = tmp_path / "prices_qfq"
    prices_dir.mkdir()
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE src (
            symbol VARCHAR, security_type VARCHAR, trade_date DATE,
            close DECIMAL(24,10), volume DECIMAL(24,4)
        )
        """
    )
    con.executemany("INSERT INTO src VALUES (?, ?, ?, ?, ?)", rows)
    con.execute(
        f"COPY src TO '{(prices_dir / 'part.parquet').as_posix()}' (FORMAT PARQUET)"
    )

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE base AS
        SELECT symbol, security_type, trade_date, close, volume,
               CAST(1 AS DECIMAL(24,4)) AS amount
        FROM read_parquet('{(prices_dir / 'part.parquet').as_posix()}')
        """
    )
    yield module, con
    con.close()


def _expected_beta_and_residual(
    closes_by_symbol: dict[str, list[Decimal]],
    market_symbols: list[str],
    all_dates: list[date],
) -> dict[tuple[str, date], tuple[float, float]]:
    def returns(series: list[Decimal]) -> dict[date, float]:
        return {
            all_dates[i]: float(series[i] / series[i - 1] - 1)
            for i in range(1, len(series))
        }

    symbol_returns = {s: returns(closes_by_symbol[s]) for s in closes_by_symbol}
    market_returns = {
        d: sum(symbol_returns[s][d] for s in market_symbols) / len(market_symbols)
        for d in all_dates[1:]
    }

    expected: dict[tuple[str, date], tuple[float, float]] = {}
    for symbol, rets in symbol_returns.items():
        ordered = [d for d in all_dates if d in rets]
        for i in range(59, len(ordered)):
            window = ordered[i - 59 : i + 1]
            xs = [rets[d] for d in window]
            ys = [market_returns[d] for d in window]
            n = 60.0
            sx, sy = sum(xs), sum(ys)
            sxy = sum(x * y for x, y in zip(xs, ys, strict=True))
            syy = sum(y * y for y in ys)
            sxx = sum(x * x for x in xs)
            var_y = syy - sy * sy / n
            beta = (sxy - sx * sy / n) / var_y
            mean_res = (sx - beta * sy) / n
            var_res = (sxx - 2 * beta * sxy + beta * beta * syy) / n - mean_res * mean_res
            expected[(symbol, window[-1])] = (beta, math.sqrt(max(var_res, 0.0)))
    return expected


def test_beta_60_residual_volatility_and_volume_pressure_match_orm_semantics(
    backfill_env,
) -> None:
    module, con = backfill_env
    factors = ["abnormal_volume_pressure", "beta_60", "residual_volatility_60"]
    con.execute(module._metrics_sql(factors))
    con.execute(module._unpivot_sql(factors))

    got = {
        (symbol, trade_date, factor_name): float(value)
        for symbol, trade_date, factor_name, value in con.execute(
            "SELECT symbol, trade_date, factor_name, value FROM long_values"
        ).fetchall()
    }

    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(70)]
    closes_by_symbol = {
        "600000.SH": [_synthetic_closes(0, i) for i in range(70)],
        "000001.SZ": [_synthetic_closes(1, i) for i in range(70)],
        "000300.SH": [_synthetic_closes(2, i) for i in range(70)],
    }
    expected = _expected_beta_and_residual(
        closes_by_symbol, market_symbols=["600000.SH", "000001.SZ"], all_dates=days
    )

    assert expected, "fixture must produce beta rows"
    assert {k[:2] for k in got if k[2] == "beta_60"} == set(expected)
    for (symbol, trade_date, factor_name), value in got.items():
        if factor_name == "beta_60":
            assert value == pytest.approx(expected[(symbol, trade_date)][0], abs=1e-8)
        elif factor_name == "residual_volatility_60":
            assert value == pytest.approx(expected[(symbol, trade_date)][1], abs=1e-8)

    # abnormal_volume_pressure: volume / mean(previous 20 volumes) - 1
    for symbol_index, symbol in enumerate(["600000.SH", "000001.SZ", "000300.SH"]):
        for i in range(20, 70):
            prev_mean = sum(
                _synthetic_volume(symbol_index, k) for k in range(i - 20, i)
            ) / Decimal(20)
            expected_avp = float(_synthetic_volume(symbol_index, i) / prev_mean - 1)
            key = (symbol, days[i], "abnormal_volume_pressure")
            assert key in got
            assert got[key] == pytest.approx(expected_avp, abs=1e-8)
        # 21-bar warmup: no value before the 21st bar
        assert (symbol, days[19], "abnormal_volume_pressure") not in got


def test_beta_60_fail_closed_on_partial_window(backfill_env) -> None:
    module, con = backfill_env
    factors = ["beta_60", "residual_volatility_60", "momentum_60"]
    con.execute(module._metrics_sql(factors))
    con.execute(module._unpivot_sql(factors))

    rows = con.execute(
        "SELECT factor_name, min(trade_date) FROM long_values GROUP BY 1 ORDER BY 1"
    ).fetchall()
    by_factor = {name: min_date for name, min_date in rows}
    # 70-day slice: the 60-return window fills on the 61st bar, so the first
    # published beta/residual value lands on day 61 (index 60).  momentum_60
    # only needs 61 bars for its lag and lands on the same day here.
    first_full_day = date(2024, 1, 1) + timedelta(days=60)
    assert by_factor["beta_60"] == first_full_day
    assert by_factor["residual_volatility_60"] == first_full_day
