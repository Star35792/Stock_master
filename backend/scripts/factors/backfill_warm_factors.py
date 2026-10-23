"""Backfill historical price-volume factors into the Parquet warm layer.

Why this script exists
----------------------

The control plane only holds a hot window of ``market.daily_bar``, so the ORM
factor services cannot compute factor values for 2025 (or any earlier) dates --
they simply do not see the input rows.  This script computes the seven purely
price-derived factors directly in DuckDB SQL over the curated ``prices_qfq``
Parquet layer (full history) and publishes them as a **partial warm-layer
release**.

A partial release only contains ``factor/factor_value``; every other table and
every untouched date is inherited from ``--parent-release`` through
``WarmLayerStore.base_release_tag``.  That keeps the control plane slim (the
values never land in DuckDB) and avoids copying ~700 MB of Parquet per run.

Factor semantics (aligned with ``app/services/basic_price_volume_factors.py``)
-----------------------------------------------------------------------------

* ``momentum_20``  : close / close[t-20] - 1
* ``momentum_60``  : close / close[t-60] - 1
* ``volatility_20``: population stddev of the trailing 19 daily returns
* ``downside_volatility_20``: RMS of the trailing 19 returns floored at 0
* ``max_drawdown_20``: worst (close / trailing running peak - 1) in 20 bars
* ``liquidity_20`` : mean ``amount`` over 20 bars
* ``volume_stability_20``: negative coefficient of variation of ``volume``

Signals use forward-adjusted (qfq) prices so dividend ex-dates do not appear as
artificial drawdowns.  Execution still uses unadjusted prices
(``_EXECUTION_ADJUST_TYPE`` in the backtest service); this script does not
change that.

Corporate-action screen
-----------------------

The backtest engine rejects any interval containing a corporate action because
event accounting is not implemented.  ``--exclude-corporate-actions`` therefore
drops symbols carrying an ``ex_date`` inside the window.  That is a real
selection bias (dividend payers are removed) and it must be recorded whenever
the produced universe is used; the result is not representative of the whole
market.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

DEFAULT_FACTORS = (
    "momentum_20",
    "momentum_60",
    "volatility_20",
    "downside_volatility_20",
    "max_drawdown_20",
    "liquidity_20",
    "volume_stability_20",
)

FACTOR_VERSION = "v1-qfq"
WARMUP_DAYS = 80  # calendar slack so the 60-day lookback is warm at start

_WINDOW_CLAUSE = """
    WINDOW
        symbol_days AS (PARTITION BY symbol ORDER BY trade_date),
        trailing_19 AS (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 18 PRECEDING AND CURRENT ROW
        ),
        trailing_20 AS (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        )
"""

# Factor values are fixed-point to match ``factor.factor_value.value``
# (``Numeric(24, 10)``); storing them as DOUBLE would break the project rule
# that core financial figures never live in floating point.
_NULL_DOUBLE = "NULL::DECIMAL(24,10)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="回填历史价量因子到 Parquet 温层")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--warm-root", default="data/warm")
    parser.add_argument("--curated-root", default="data/warm/curated")
    parser.add_argument("--db-path", default="data/stock_master.duckdb")
    parser.add_argument(
        "--parent-release", help="父 release tag（子 release 继承其未发布的表）"
    )
    parser.add_argument("--release-tag", help="子 release tag，默认当前时间戳")
    parser.add_argument("--factors", help="逗号分隔，默认全部 7 个")
    parser.add_argument(
        "--factor-version",
        default=FACTOR_VERSION,
        help="写入 factor_value.factor_version 的版本标签",
    )
    parser.add_argument(
        "--exclude-corporate-actions",
        action="store_true",
        help="剔除窗口内有公司行为的证券（使回测可通过 reject 闸门）",
    )
    parser.add_argument(
        "--require-complete-coverage",
        action="store_true",
        help="剔除窗口内任一交易日缺少可信行情的证券（避免回测引擎因缺 bar 整次失败）",
    )
    parser.add_argument("--symbols", help="可选：逗号分隔的证券子集")
    parser.add_argument("--skip-verify", action="store_true")
    return parser


def _resolve_excluded_symbols(
    control_db: Path, start_date: date, end_date: date
) -> list[str]:
    con = duckdb.connect(str(control_db), read_only=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT symbol FROM market.corporate_action "
            "WHERE ex_date BETWEEN ? AND ? ORDER BY symbol",
            [start_date, end_date],
        ).fetchall()
    finally:
        con.close()
    return [row[0] for row in rows]


def _resolve_incomplete_symbols(
    prices_glob: str, start_date: date, end_date: date
) -> list[str]:
    """Symbols missing a trusted bar on at least one trading day of the window.

    The backtest engine fails the whole run when a scheduled execution date has
    no trusted bar, and ``market.security_status_event`` is still empty so a
    missing bar cannot be told apart from a real suspension.  Screening for
    complete coverage is therefore the honest option: it never asserts a
    suspension we have no source for, and it is recorded as an explicit
    selection bias.
    """
    con = duckdb.connect()
    try:
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE bars AS
            SELECT symbol, trade_date
            FROM read_parquet('{prices_glob}', hive_partitioning=true)
            WHERE trade_date BETWEEN DATE '{start_date}' AND DATE '{end_date}'
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE calendar AS
            SELECT DISTINCT trade_date FROM bars
            """
        )
        rows = con.execute(
            """
            WITH expected AS (SELECT count(*) AS n FROM calendar),
            observed AS (
                SELECT symbol, count(DISTINCT trade_date) AS n
                FROM bars GROUP BY symbol
            )
            SELECT o.symbol
            FROM observed o, expected e
            WHERE o.n < e.n
            ORDER BY o.symbol
            """
        ).fetchall()
    finally:
        con.close()
    return [row[0] for row in rows]


def _metrics_sql(factors: list[str]) -> str:
    """Build the wide per-symbol metric table for the requested factors."""
    wanted = set(factors)

    def pick(name: str, sql: str, alias: str) -> str:
        return sql if name in wanted else f"{_NULL_DOUBLE} AS {alias}"

    lag_20 = pick(
        "momentum_20", "LAG(close, 20) OVER symbol_days AS close_20", "close_20"
    )
    lag_60 = pick(
        "momentum_60", "LAG(close, 60) OVER symbol_days AS close_60", "close_60"
    )
    vol = pick(
        "volatility_20", "stddev_pop(ret) OVER trailing_19 AS volatility_20", "volatility_20"
    )
    downside = pick(
        "downside_volatility_20",
        "sqrt(avg(CASE WHEN ret < 0 THEN ret * ret ELSE 0 END) "
        "OVER trailing_19) AS downside_volatility_20",
        "downside_volatility_20",
    )
    drawdown = pick(
        "max_drawdown_20",
        "MIN(close / NULLIF(peak_20, 0) - 1) OVER trailing_20 "
        "AS max_drawdown_20",
        "max_drawdown_20",
    )
    liquidity = pick(
        "liquidity_20", "avg(amount) OVER trailing_20 AS liquidity_20", "liquidity_20"
    )
    stability = pick(
        "volume_stability_20",
        "-1 * stddev_pop(volume) OVER trailing_20 "
        "/ NULLIF(avg(volume) OVER trailing_20, 0) "
        "AS volume_stability_20",
        "volume_stability_20",
    )
    return f"""
        CREATE OR REPLACE TEMP TABLE metrics AS
        WITH lags AS (
            SELECT
                symbol,
                trade_date,
                close,
                volume,
                amount,
                LAG(close, 1) OVER symbol_days AS close_prev,
                {lag_20},
                {lag_60},
                MAX(close) OVER trailing_20 AS peak_20
            FROM base
            {_WINDOW_CLAUSE}
        ),
        numbered AS (
            SELECT *, close / NULLIF(close_prev, 0) - 1 AS ret
            FROM lags
        )
        SELECT
            symbol,
            trade_date,
            close,
            close_20,
            close_60,
            {vol},
            {downside},
            {drawdown},
            {liquidity},
            {stability}
        FROM numbered
        {_WINDOW_CLAUSE}
    """


def _unpivot_sql(factors: list[str]) -> str:
    """Turn the wide metric table into long ``(symbol, trade_date, name, value)``."""
    expressions = {
        "momentum_20": "close / NULLIF(close_20, 0) - 1",
        "momentum_60": "close / NULLIF(close_60, 0) - 1",
        "volatility_20": "volatility_20",
        "downside_volatility_20": "downside_volatility_20",
        "max_drawdown_20": "max_drawdown_20",
        "liquidity_20": "liquidity_20",
        "volume_stability_20": "volume_stability_20",
    }
    selects = "\nUNION ALL\n".join(
        f"SELECT symbol, trade_date, '{name}' AS factor_name, "
        f"CAST({expressions[name]} AS DECIMAL(24,10)) AS value FROM metrics"
        for name in factors
    )
    return f"""
        CREATE OR REPLACE TEMP TABLE long_values AS
        SELECT symbol, trade_date, factor_name, value
        FROM (
            {selects}
        )
        WHERE value IS NOT NULL
    """


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    warm_root = Path(args.warm_root)
    curated = Path(args.curated_root) / "prices_qfq"
    if not curated.exists():
        print(f"curated prices_qfq not found: {curated}")
        return 1

    factors = (
        [f.strip() for f in args.factors.split(",") if f.strip()]
        if args.factors
        else list(DEFAULT_FACTORS)
    )
    unknown = [f for f in factors if f not in DEFAULT_FACTORS]
    if unknown:
        print(f"unsupported factors: {unknown}")
        return 1

    parent = args.parent_release
    if parent is None:
        candidates = sorted(
            p.name
            for p in (warm_root / "exported").iterdir()
            if p.is_dir() and (p / "export_summary.json").exists()
        )
        if not candidates:
            print("no parent release found")
            return 1
        parent = candidates[-1]
    release_tag = args.release_tag or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    control_db = Path(args.db_path)
    excluded: list[str] = []
    if args.exclude_corporate_actions:
        excluded = _resolve_excluded_symbols(
            control_db, args.start_date, args.end_date
        )
        print(f"公司行为剔除 {len(excluded):,} 只证券")

    if args.require_complete_coverage:
        prices_glob = f"{curated.as_posix()}/**/*.parquet"
        incomplete = _resolve_incomplete_symbols(
            prices_glob, args.start_date, args.end_date
        )
        print(f"行情不完整剔除 {len(incomplete):,} 只证券")
        excluded = sorted(set(excluded) | set(incomplete))

    symbol_filter: list[str] = []
    if args.symbols:
        symbol_filter = [
            s.strip().upper() for s in args.symbols.split(",") if s.strip()
        ]

    con = duckdb.connect()
    con.execute("SET threads = 4")
    prices_glob = f"{curated.as_posix()}/**/*.parquet"
    warmup_date = date.fromordinal(args.start_date.toordinal() - WARMUP_DAYS)

    extra_filters = ""
    if excluded:
        quoted = ",".join(f"'{s}'" for s in excluded)
        extra_filters += f" AND symbol NOT IN ({quoted})"
    if symbol_filter:
        quoted = ",".join(f"'{s}'" for s in symbol_filter)
        extra_filters += f" AND symbol IN ({quoted})"

    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE base AS
        SELECT symbol, trade_date, close, volume, amount
        FROM read_parquet('{prices_glob}', hive_partitioning=true)
        WHERE trade_date BETWEEN DATE '{warmup_date}'
          AND DATE '{args.end_date}'
          AND close IS NOT NULL AND close > 0
        {extra_filters}
        """
    )
    con.execute(_metrics_sql(factors))
    con.execute(_unpivot_sql(factors))

    window_filter = (
        f"WHERE trade_date BETWEEN DATE '{args.start_date}' "
        f"AND DATE '{args.end_date}'"
    )
    total_rows = con.execute(
        f"SELECT count(*) FROM long_values {window_filter}"
    ).fetchone()[0]
    if total_rows == 0:
        print("no factor rows produced; aborting without writing a release")
        return 1

    stats = con.execute(
        "SELECT factor_name, count(*), count(DISTINCT symbol), "
        f"count(DISTINCT trade_date) FROM long_values {window_filter} "
        "GROUP BY 1 ORDER BY 1"
    ).fetchall()

    out_dir = warm_root / "exported" / release_tag / "factor" / "factor_value"
    out_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    con.execute(
        f"""
        COPY (
            SELECT
                symbol,
                trade_date,
                factor_name,
                '{args.factor_version}' AS factor_version,
                value,
                NULL::BIGINT AS run_id,
                TIMESTAMPTZ '{now}' AS updated_at,
                TIMESTAMPTZ '{now}' AS created_at,
                strftime(trade_date, '%Y') AS trade_year,
                CAST(strftime(trade_date, '%m') AS INTEGER) AS trade_month
            FROM long_values
            {window_filter}
        ) TO '{out_dir.as_posix()}'
        (FORMAT PARQUET, PARTITION_BY (trade_year, trade_month))
        """
    )

    if not args.skip_verify:
        written = con.execute(
            f"SELECT count(*) FROM read_parquet('{out_dir.as_posix()}/**/*.parquet', "
            "hive_partitioning=true)"
        ).fetchone()[0]
        if written != total_rows:
            print(f"校验失败: 源 {total_rows} 行 != Parquet {written} 行")
            return 1
        print(f"校验通过: {written:,} 行")

    summary = {
        "release_tag": release_tag,
        "base_release_tag": parent,
        "partial_release": True,
        "tables": [
            {
                "schema": "factor",
                "table": "factor_value",
                "row_count": total_rows,
                "min_date": str(args.start_date),
                "max_date": str(args.end_date),
                "output_dir": str(out_dir.relative_to(warm_root.parent.parent)),
            }
        ],
        "all_ok": True,
        "notes": [
            "partial release; unlisted tables inherit from base_release_tag",
            f"computed from curated prices_qfq, version={args.factor_version}",
        ],
    }
    if excluded:
        summary["excluded_symbols"] = len(excluded)
        summary["exclusion_bias"] = (
            "窗口内有公司行为或缺少可信行情的证券被剔除；红利支付者与停牌股被系统性"
            "排除，结果不代表全市场"
        )
    summary_path = warm_root / "exported" / release_tag / "export_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\nrelease = {release_tag} (parent = {parent})")
    print(f"窗口 {args.start_date} ~ {args.end_date}")
    print(f"总行数 = {total_rows:,}")
    print(f"{'factor':<26}{'rows':>12}{'symbols':>10}{'days':>8}")
    for name, rows, syms, days in stats:
        print(f"  {name:<24}{rows:>12,}{syms:>10,}{days:>8}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
