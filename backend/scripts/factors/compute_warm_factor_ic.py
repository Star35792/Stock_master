"""Compute factor IC / RankIC evidence from the warm layer.

Why this script exists
----------------------

``factor.factor_profile`` was empty, so ``factor_pool_classifier`` judged all 71
registered factors ``frozen`` (it requires ``min_trade_dates=200``).  The
in-platform batch profiler reads through the ORM and therefore only sees the
control-plane hot window (a handful of days), which can never satisfy that
threshold.  This script computes the same evidence directly over the Parquet
warm layer for a multi-month window and persists it into
``factor.factor_profile``, so classification finally has something to judge.

Method
------

* Factor exposures are read from the warm layer (Parquet) at ``trade_date``.
* Forward returns are computed on **qfq** prices (``prices_qfq``) so ex-dividend
  gaps do not masquerade as returns: ``close[t+h] / close[t] - 1``.
* ``ic`` is the Pearson correlation across the cross-section on that date;
  ``rank_ic`` is the same correlation applied to within-date ranks.
* Only symbols with both an exposure and a realised forward return enter the
  correlation, and ``coverage_rate`` records how complete that was.

Caveats (recorded in the output)
--------------------------------

The universe inherits the same screens used to build the factors: symbols with
corporate actions in the window and symbols with incomplete price coverage were
excluded.  Survivorship bias is **not** corrected, and there is no out-of-sample
window.  The numbers describe this window's sample only.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from app.config.settings import settings
from app.database.session import SessionLocal
from app.models.factor.factor_profile import FactorProfile
from app.repositories.warm_layer import WarmLayerStore

HORIZONS = (1, 5, 20)
QUANTILE_COUNT = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从温层计算因子 IC 证据")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--factor-version", default="v1-qfq")
    parser.add_argument(
        "--release-tag",
        default=None,
        help="温层 release tag；默认取 settings.research_release_tag 或最新 release",
    )
    parser.add_argument("--warm-root", default=None)
    parser.add_argument("--curated-root", default="data/warm/curated")
    parser.add_argument("--code-version", default="local")
    parser.add_argument("--out", help="聚合证据表落盘路径")
    parser.add_argument("--no-persist", action="store_true", help="只出报告不落库")
    return parser


def _load_daily_ic(
    con: duckdb.DuckDBPyConnection, prices_glob: str, start: date, end: date, version: str
) -> list[dict[str, object]]:
    """Return one row per (factor, trade_date) with cross-sectional statistics."""
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE fv AS
        SELECT symbol, trade_date, factor_name, CAST(value AS DOUBLE) AS value
        FROM factor.factor_value
        WHERE factor_version = '{version}'
          AND trade_date BETWEEN DATE '{start}' AND DATE '{end}'
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE px AS
        SELECT
            symbol,
            trade_date,
            close,
            LEAD(close, 1) OVER symbol_days AS c1,
            LEAD(close, 5) OVER symbol_days AS c5,
            LEAD(close, 20) OVER symbol_days AS c20
        FROM read_parquet('{prices_glob}', hive_partitioning=true)
        WHERE trade_date BETWEEN DATE '{start}' AND DATE '{end}'
        WINDOW symbol_days AS (PARTITION BY symbol ORDER BY trade_date)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE joined AS
        SELECT
            f.factor_name,
            f.trade_date,
            f.symbol,
            f.value,
            p.c1 / NULLIF(p.close, 0) - 1 AS fwd1,
            p.c5 / NULLIF(p.close, 0) - 1 AS fwd5,
            p.c20 / NULLIF(p.close, 0) - 1 AS fwd20
        FROM fv f
        JOIN px p ON p.symbol = f.symbol AND p.trade_date = f.trade_date
        WHERE p.close > 0
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE scored AS
        SELECT
            *,
            rank() OVER (
                PARTITION BY factor_name, trade_date ORDER BY value
            ) AS rk_value,
            rank() OVER (
                PARTITION BY factor_name, trade_date ORDER BY fwd1
            ) AS rk_fwd1,
            rank() OVER (
                PARTITION BY factor_name, trade_date ORDER BY fwd5
            ) AS rk_fwd5,
            rank() OVER (
                PARTITION BY factor_name, trade_date ORDER BY fwd20
            ) AS rk_fwd20,
            ntile(5) OVER (
                PARTITION BY factor_name, trade_date ORDER BY value
            ) AS bucket
        FROM joined
        """
    )
    rows = con.execute(
        """
        WITH universe AS (
            SELECT factor_name, trade_date, count(*) AS universe_size
            FROM fv GROUP BY 1, 2
        ),
        per_date AS (
            SELECT
                factor_name,
                trade_date,
                count(*) AS observation_count,
                corr(value, fwd1) AS ic_1d,
                corr(rk_value, rk_fwd1) AS rank_ic_1d,
                corr(value, fwd5) AS ic_5d,
                corr(rk_value, rk_fwd5) AS rank_ic_5d,
                corr(value, fwd20) AS ic_20d,
                corr(rk_value, rk_fwd20) AS rank_ic_20d,
                avg(value) AS mean_value,
                stddev_pop(value) AS sd_value,
                min(value) AS min_value,
                max(value) AS max_value,
                quantile_cont(value, 0.01) AS p01_value,
                quantile_cont(value, 0.99) AS p99_value,
                count(*) FILTER (
                    WHERE abs(value) > 10
                ) AS extreme_count,
                avg(fwd1) FILTER (WHERE bucket = 5) AS top_q,
                avg(fwd1) FILTER (WHERE bucket = 1) AS bottom_q
            FROM scored
            WHERE fwd1 IS NOT NULL
            GROUP BY 1, 2
        )
        SELECT
            p.factor_name,
            p.trade_date,
            u.universe_size,
            p.observation_count,
            p.ic_1d, p.rank_ic_1d, p.ic_5d, p.rank_ic_5d, p.ic_20d, p.rank_ic_20d,
            p.mean_value, p.sd_value, p.min_value, p.max_value,
            p.p01_value, p.p99_value, p.extreme_count, p.top_q, p.bottom_q
        FROM per_date p
        JOIN universe u ON u.factor_name = p.factor_name
                        AND u.trade_date = p.trade_date
        ORDER BY p.factor_name, p.trade_date
        """
    ).fetchall()
    keys = [
        "factor_name", "trade_date", "universe_size", "observation_count",
        "ic_1d", "rank_ic_1d", "ic_5d", "rank_ic_5d", "ic_20d", "rank_ic_20d",
        "mean_value", "sd_value", "min_value", "max_value",
        "p01_value", "p99_value", "extreme_count", "top_q", "bottom_q",
    ]
    return [dict(zip(keys, row, strict=True)) for row in rows]


def _clean(value: object) -> float | None:
    """Drop NULL / NaN / inf.

    DuckDB ``corr()`` returns NaN rather than NULL for degenerate groups (for
    example the last dates of the window, where the 20-day forward return has no
    observations yet).  Casting NaN into ``DECIMAL(24, 10)`` raises a conversion
    error, so every value crossing into the database is sanitised here.
    """
    if value is None:
        return None
    number = float(value)
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _quantize(value: object) -> Decimal | None:
    number = _clean(value)
    if number is None:
        return None
    return Decimal(str(round(number, 10)))


def _aggregate(daily: list[dict[str, object]]) -> list[dict[str, object]]:
    by_factor: dict[str, list[dict[str, object]]] = {}
    for row in daily:
        by_factor.setdefault(str(row["factor_name"]), []).append(row)

    summary: list[dict[str, object]] = []
    for factor, rows in sorted(by_factor.items()):
        entry: dict[str, object] = {"factor": factor, "days": len(rows)}
        for horizon in ("1d", "5d", "20d"):
            for kind in ("ic", "rank_ic"):
                values = [
                    cleaned
                    for cleaned in (
                        _clean(r.get(f"{kind}_{horizon}")) for r in rows
                    )
                    if cleaned is not None
                ]
                if not values:
                    entry[f"{kind}_{horizon}_mean"] = None
                    entry[f"{kind}_{horizon}_ir"] = None
                    entry[f"{kind}_{horizon}_positive_rate"] = None
                    continue
                mean = sum(values) / len(values)
                variance = sum((v - mean) ** 2 for v in values) / len(values)
                sd = variance**0.5
                positive = sum(1 for v in values if v > 0) / len(values)
                entry[f"{kind}_{horizon}_mean"] = round(mean, 6)
                entry[f"{kind}_{horizon}_ir"] = round(mean / sd, 6) if sd else None
                entry[f"{kind}_{horizon}_positive_rate"] = round(positive, 4)
        spreads = []
        for r in rows:
            top = _clean(r.get("top_q"))
            bottom = _clean(r.get("bottom_q"))
            if top is None or bottom is None:
                continue
            spreads.append(top - bottom)
        entry["top_minus_bottom_1d_mean"] = (
            round(sum(spreads) / len(spreads), 6) if spreads else None
        )
        entry["avg_universe"] = round(
            sum(int(r["universe_size"]) for r in rows) / len(rows), 1
        )
        summary.append(entry)
    return summary


def _persist(session, daily: list[dict[str, object]], version: str, code_version: str) -> int:
    written = 0
    for row in daily:
        universe = int(row["universe_size"])
        observed = int(row["observation_count"])
        coverage = Decimal(observed) / Decimal(universe) if universe else Decimal("0")
        session.merge(
            FactorProfile(
                factor_name=str(row["factor_name"]),
                factor_version=version,
                trade_date=row["trade_date"],
                universe_size=universe,
                observation_count=observed,
                missing_count=max(universe - observed, 0),
                coverage_rate=coverage.quantize(Decimal("0.0000000001")),
                missing_rate=(Decimal("1") - coverage).quantize(Decimal("0.0000000001")),
                mean=_quantize(row["mean_value"]),
                standard_deviation=_quantize(row["sd_value"]),
                minimum=_quantize(row["min_value"]),
                maximum=_quantize(row["max_value"]),
                p01=_quantize(row["p01_value"]),
                p99=_quantize(row["p99_value"]),
                extreme_count=int(row["extreme_count"] or 0),
                ic_1d=_quantize(row["ic_1d"]),
                rank_ic_1d=_quantize(row["rank_ic_1d"]),
                ic_5d=_quantize(row["ic_5d"]),
                rank_ic_5d=_quantize(row["rank_ic_5d"]),
                ic_20d=_quantize(row["ic_20d"]),
                rank_ic_20d=_quantize(row["rank_ic_20d"]),
                top_quantile_forward_return_1d=_quantize(row["top_q"]),
                bottom_quantile_forward_return_1d=_quantize(row["bottom_q"]),
                long_short_forward_return_1d=(
                    None
                    if _clean(row["top_q"]) is None or _clean(row["bottom_q"]) is None
                    else _quantize(
                        float(_clean(row["top_q"]))
                        - float(_clean(row["bottom_q"]))
                    )
                ),
                quantile_monotonicity_1d=None,
                top_quantile_turnover=None,
                code_version=code_version,
            )
        )
        written += 1
    session.commit()
    return written


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = (
        WarmLayerStore(args.warm_root) if args.warm_root else WarmLayerStore()
    )
    tag = args.release_tag or settings.research_release_tag or store.latest_release_tag
    base = store.base_release_tag_for(tag) if tag else None
    if base:
        store = WarmLayerStore(
            args.warm_root or store.warm_root, base_release_tag=base
        )
    # Parquet-only: leaves the control-plane file handle free for ORM writes.
    con = store.connect(release_tag=tag, read_only=True, attach_control=False)
    curated = Path(args.curated_root) / "prices_qfq"
    prices_glob = f"{curated.as_posix()}/**/*.parquet"
    try:
        daily = _load_daily_ic(
            con, prices_glob, args.start_date, args.end_date, args.factor_version
        )
    finally:
        con.close()

    if not daily:
        print("no observations; aborting")
        return 1

    summary = _aggregate(daily)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": [str(args.start_date), str(args.end_date)],
        "factor_version": args.factor_version,
        "release_tag": tag,
        "daily_rows": len(daily),
        "caveats": [
            "universe excludes symbols with corporate actions in the window",
            "universe excludes symbols with incomplete trusted price coverage",
            "survivorship bias not corrected (no delisting dates available)",
            "single window, no out-of-sample validation",
            "forward returns use qfq prices; execution would use unadjusted prices",
        ],
        "factors": summary,
    }

    print(f"release = {tag}")
    print(f"窗口 {args.start_date} ~ {args.end_date}  逐日行数 = {len(daily):,}")
    print(
        f"\n{'factor':<24}{'days':>6}{'RankIC1':>10}{'IR1':>8}{'pos%':>7}"
        f"{'RankIC5':>10}{'pos%':>7}{'RankIC20':>11}{'pos%':>7}"
    )
    for item in summary:
        print(
            f"{item['factor']:<24}{item['days']:>6}"
            f"{_fmt(item['rank_ic_1d_mean']):>10}{_fmt(item['rank_ic_1d_ir']):>8}"
            f"{_fmt(item['rank_ic_1d_positive_rate']):>7}"
            f"{_fmt(item['rank_ic_5d_mean']):>10}"
            f"{_fmt(item['rank_ic_5d_positive_rate']):>7}"
            f"{_fmt(item['rank_ic_20d_mean']):>11}"
            f"{_fmt(item['rank_ic_20d_positive_rate']):>7}"
        )

    if not args.no_persist:
        with SessionLocal() as db:
            written = _persist(db, daily, args.factor_version, args.code_version)
        print(f"\n已写入 factor.factor_profile: {written:,} 行")
    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已落盘: {args.out}")
    return 0


def _fmt(value: object) -> str:
    return "-" if value is None else f"{value}"


if __name__ == "__main__":
    raise SystemExit(main())
