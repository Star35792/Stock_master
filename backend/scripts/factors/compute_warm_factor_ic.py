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
  correlation, and ``coverage_rate`` records how complete that was.  The
  denominator is the **price** universe (securities with a trusted close that
  day), not the factor's own rows: taking it from the factor's own rows would
  make the ratio identically 1.0 and hide the coverage gap.
* The **endpoint** spreads (top minus bottom quantile, 1/5/20 days) and the
  quantile monotonicity and turnover diagnostics are computed as well.  The
  endpoint is the tradability evidence the factor pool classifier gates on: a
  factor can carry a positive IC while its extreme buckets still lose money.

Idempotence
------------

Re-running this script refreshes the existing rows instead of failing.  The
persistence path uses ``FactorProfileRepository.upsert`` (``ON CONFLICT DO
UPDATE`` on ``(factor_name, factor_version, trade_date)``); a plain
``session.merge`` degrades to an INSERT when the ORM object carries no primary
key and would hit the unique constraint on the second run.

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
from app.factors.quantile_diagnostics import prepare_tie_aware_quantiles
from app.repositories.factor import FactorProfileRepository
from app.repositories.warm_layer import WarmLayerStore

HORIZONS = (1, 5, 20)
QUANTILE_COUNT = 5

#: Minimum number of observations in a cross-section for the IC to be
#: meaningful.  Matches ``compute_value_factor_ic.py``.
MIN_CROSS_SECTION = 50

#: Share of the cross-section used for the top/bottom quantile spread and the
#: turnover diagnostic.
QUANTILE_SHARE = Decimal("0.2")


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
    # The price universe: securities with a trusted close that day.  This is
    # the denominator of ``coverage_rate`` so that a factor which only covers
    # part of the market is visible as such.  Taking the denominator from the
    # factor's own rows would make the ratio identically 1.0 and hide the gap.
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE universe AS
        SELECT trade_date, COUNT(DISTINCT symbol) AS universe_size
        FROM px
        WHERE close > 0
        GROUP BY 1
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
    prepare_tie_aware_quantiles(
        con,
        quantile_count=QUANTILE_COUNT,
        endpoint_share=QUANTILE_SHARE,
    )
    rows = con.execute(
        f"""
        WITH per_date AS (
            SELECT
                factor_name,
                trade_date,
                count(*) AS observation_count,
                corr(value, fwd1) AS ic_1d,
                corr(rk_value_1d, rk_fwd1) AS rank_ic_1d,
                corr(value, fwd5) AS ic_5d,
                corr(rk_value_5d, rk_fwd5) AS rank_ic_5d,
                corr(value, fwd20) AS ic_20d,
                corr(rk_value_20d, rk_fwd20) AS rank_ic_20d,
                avg(value) AS mean_value,
                stddev_pop(value) AS sd_value,
                min(value) AS min_value,
                max(value) AS max_value,
                quantile_cont(value, 0.01) AS p01_value,
                quantile_cont(value, 0.99) AS p99_value,
                count(*) FILTER (
                    WHERE abs(value) > 10
                ) AS extreme_count,
                avg(fwd1) FILTER (WHERE value >= top_boundary_1d) AS top_q_1d,
                avg(fwd1) FILTER (WHERE value <= bottom_boundary_1d) AS bottom_q_1d,
                avg(fwd5) FILTER (WHERE value >= top_boundary_5d) AS top_q_5d,
                avg(fwd5) FILTER (WHERE value <= bottom_boundary_5d) AS bottom_q_5d,
                avg(fwd20) FILTER (WHERE value >= top_boundary_20d) AS top_q_20d,
                avg(fwd20) FILTER (WHERE value <= bottom_boundary_20d) AS bottom_q_20d
            FROM scored
            WHERE fwd1 IS NOT NULL
            GROUP BY 1, 2
            HAVING count(*) >= {MIN_CROSS_SECTION}
        ),
        mono AS (
            SELECT
                factor_name,
                trade_date,
                corr(bucket, bucket_mean_fwd1) AS monotonicity_1d
            FROM bucket_returns
            GROUP BY 1, 2
        )
        SELECT
            p.factor_name,
            p.trade_date,
            u.universe_size,
            p.observation_count,
            p.ic_1d, p.rank_ic_1d, p.ic_5d, p.rank_ic_5d, p.ic_20d, p.rank_ic_20d,
            p.mean_value, p.sd_value, p.min_value, p.max_value,
            p.p01_value, p.p99_value, p.extreme_count,
            p.top_q_1d, p.bottom_q_1d, p.top_q_5d, p.bottom_q_5d,
            p.top_q_20d, p.bottom_q_20d,
            m.monotonicity_1d,
            t.top_quantile_turnover
        FROM per_date p
        JOIN universe u ON u.trade_date = p.trade_date
        LEFT JOIN mono m
            ON m.factor_name = p.factor_name AND m.trade_date = p.trade_date
        LEFT JOIN top_turnover t
            ON t.factor_name = p.factor_name AND t.trade_date = p.trade_date
        ORDER BY p.factor_name, p.trade_date
        """
    ).fetchall()
    keys = [
        "factor_name", "trade_date", "universe_size", "observation_count",
        "ic_1d", "rank_ic_1d", "ic_5d", "rank_ic_5d", "ic_20d", "rank_ic_20d",
        "mean_value", "sd_value", "min_value", "max_value",
        "p01_value", "p99_value", "extreme_count",
        "top_q_1d", "bottom_q_1d", "top_q_5d", "bottom_q_5d",
        "top_q_20d", "bottom_q_20d",
        "monotonicity_1d", "top_quantile_turnover",
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
        for horizon in ("1d", "5d", "20d"):
            spreads = []
            for r in rows:
                top = _clean(r.get(f"top_q_{horizon}"))
                bottom = _clean(r.get(f"bottom_q_{horizon}"))
                if top is None or bottom is None:
                    continue
                spreads.append(top - bottom)
            entry[f"top_minus_bottom_{horizon}_mean"] = (
                round(sum(spreads) / len(spreads), 6) if spreads else None
            )
            entry[f"top_minus_bottom_{horizon}_positive_rate"] = (
                round(sum(1 for s in spreads if s > 0) / len(spreads), 4)
                if spreads
                else None
            )
        monotonicity = [
            cleaned
            for cleaned in (_clean(r.get("monotonicity_1d")) for r in rows)
            if cleaned is not None
        ]
        entry["mean_quantile_monotonicity_1d"] = (
            round(sum(monotonicity) / len(monotonicity), 6) if monotonicity else None
        )
        turnover = [
            cleaned
            for cleaned in (_clean(r.get("top_quantile_turnover")) for r in rows)
            if cleaned is not None
        ]
        entry["mean_top_quantile_turnover"] = (
            round(sum(turnover) / len(turnover), 6) if turnover else None
        )
        entry["avg_universe"] = round(
            sum(int(r["universe_size"]) for r in rows) / len(rows), 1
        )
        summary.append(entry)
    return summary


def _spread(row: dict[str, object], horizon: str) -> Decimal | None:
    """Top-minus-bottom quantile spread for one horizon."""
    top = _clean(row.get(f"top_q_{horizon}"))
    bottom = _clean(row.get(f"bottom_q_{horizon}"))
    if top is None or bottom is None:
        return None
    return _quantize(top - bottom)


def _persist(session, daily: list[dict[str, object]], version: str, code_version: str) -> int:
    """Persist the daily evidence into ``factor.factor_profile``.

    Uses :meth:`FactorProfileRepository.upsert` rather than ``session.merge``:
    ``merge`` degrades to a plain INSERT when the ORM object carries no primary
    key, so a re-run hits the ``(factor_name, factor_version, trade_date)``
    unique constraint instead of refreshing the existing row.
    """
    repository = FactorProfileRepository()
    written = 0
    for row in daily:
        universe = int(row["universe_size"])
        observed = int(row["observation_count"])
        coverage = Decimal(observed) / Decimal(universe) if universe else Decimal("0")
        repository.upsert(
            session,
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
            top_quantile_forward_return_1d=_quantize(row["top_q_1d"]),
            bottom_quantile_forward_return_1d=_quantize(row["bottom_q_1d"]),
            long_short_forward_return_1d=_spread(row, "1d"),
            long_short_forward_return_5d=_spread(row, "5d"),
            long_short_forward_return_20d=_spread(row, "20d"),
            quantile_monotonicity_1d=_quantize(row.get("monotonicity_1d")),
            top_quantile_turnover=_quantize(row.get("top_quantile_turnover")),
            # This flag certifies the input provenance, not the strategy's
            # out-of-sample performance or the backtest universe.
            evidence_eligibility="strategy_eligible",
            evidence_eligibility_reason=(
                "warm price-volume inputs use only trusted historical market observations"
            ),
            code_version=code_version,
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
    print(
        f"\n{'factor':<24}{'LS-1d':>12}{'pos%':>8}{'LS-5d':>12}"
        f"{'pos%':>8}{'LS-20d':>12}{'pos%':>8}{'mono1d':>9}{'turnover':>10}"
    )
    for item in summary:
        print(
            f"{item['factor']:<24}"
            f"{_fmt(item['top_minus_bottom_1d_mean']):>12}"
            f"{_fmt(item['top_minus_bottom_1d_positive_rate']):>8}"
            f"{_fmt(item['top_minus_bottom_5d_mean']):>12}"
            f"{_fmt(item['top_minus_bottom_5d_positive_rate']):>8}"
            f"{_fmt(item['top_minus_bottom_20d_mean']):>12}"
            f"{_fmt(item['top_minus_bottom_20d_positive_rate']):>8}"
            f"{_fmt(item['mean_quantile_monotonicity_1d']):>9}"
            f"{_fmt(item['mean_top_quantile_turnover']):>10}"
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
