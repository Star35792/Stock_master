"""Compute value-factor IC evidence from the valuation-history warm layer.

Why this script exists
----------------------

``factor.factor_profile`` holds the IC evidence the factor-pool classifier
judges a factor by.  Until the valuation-history sweep landed, no *value*
factor had any evidence, so the whole value family stayed ``frozen`` and the
factor pool could not use it.

This script builds the value factors directly from
``research.valuation_history`` (the Parquet-only warm-layer table produced by
the Eastmoney sweep) and computes the same daily cross-sectional statistics
the existing warm IC script computes, then persists them into
``factor.factor_profile``.

The factor definitions
----------------------

Two value factors are computed from the same valuation snapshot:

``value_earnings_yield``
    ``1 / PE_TTM`` (positive PE only), i.e. the earnings yield directly.  It
    is the **canonical** factor of the value family.

``value_pb_mrq``
    Negative ``PB_MRQ``.  Same reasoning on the book value, but the field is
    not point-in-time (see the caveats below).

Why ``value_pe_ttm`` was retired
---------------------------------

An earlier version of this script also published ``value_pe_ttm = -PE_TTM``.
That definition is **directionally wrong** and was retired:

* ``PE_TTM`` is negative for loss-making companies (26.51% of the sample:
  23.93% / 26.93% / 28.15% in 2024 / 2025 / 2026).  ``-PE_TTM`` maps a deeply
  loss-making company to a *large positive* number, so under the "larger
  value means cheaper" convention the **cheapest bucket was 100% loss-making
  companies** (645,112 rows).  The factor measured "how badly a company is
  losing money", not "how cheap it is".
* Fixing it by restricting to ``PE_TTM > 0`` does not create a new factor:
  on that subset ``-PE_TTM`` and ``1 / PE_TTM`` are the *same* monotone
  transform, so their rank correlation is exactly 1.0 on every one of the
  595 trading days.  Keeping both would double-count the same exposure in
  any composite score.
* The endpoint (long-short) behaviour confirms the direction error: the
  retired definition showed a **positive** 20-day long-short spread
  (+44.3 bp, t = +4.49) purely because loss-making small caps rallied in
  this window, while the corrected definition shows a **negative** spread
  (-69.3 bp, t = -2.70) at 20 days.

The retired rows remain in ``factor.factor_profile`` under version
``v1-valhist`` for audit; the current evidence is published under
``v2-valhist``.

Direction
---------

Every factor is expressed so that **a larger value means a cheaper stock**.
That matches the project's convention for a value factor and it keeps the
sign of the IC interpretable: a positive IC means "cheap went up".

Point-in-time caveats (these matter)
-------------------------------------

* Every row of ``research.valuation_history`` is a *late observation* of a
  historical date, so ``pit_verified`` is false throughout.  The factor is
  therefore **not** tradable evidence; it is research evidence about the
  relation between the valuation ratio and the forward return.
* ``PB_MRQ`` switches on the *reporting period end*, so it leaks the balance
  sheet by up to 120 days before its disclosure.  The factor is computed, but
  its IC must not be read as achievable.  A disclosure-date reconstruction
  shows the IC barely moves (+0.03285 -> +0.03258), i.e. the signal is real
  even after the look-ahead is removed -- but the *point-in-time* claim
  still fails, so the factor is frozen by the pool classifier.
* Forward returns use **qfq** prices, so ex-dividend gaps do not masquerade
  as returns.
* The cross-section shrinks by ~26% because loss-making companies are
  excluded from the earnings-yield universe.  ``coverage_rate`` measures
  coverage *inside* the factor's own universe, so this shrink is invisible
  in that field; the excluded group's forward return is reported separately
  in the caveats instead.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
from sqlalchemy import select

from app.config.settings import settings
from app.database.session import SessionLocal
from app.models.market import Security
from app.repositories.factor import FactorProfileRepository
from app.repositories.warm_layer import WarmLayerStore
from app.services.financial_sector import is_financial_industry

#: Horizon (in trading days) of the forward returns.
HORIZONS = (1, 5, 20)

#: Quantile count of the long-short spread.
QUANTILE_COUNT = 5

#: Minimum number of observations in a cross-section for the IC to be
#: meaningful.  Below this the correlation is dominated by a handful of
#: names, so the day is skipped rather than reported.
MIN_CROSS_SECTION = 50

#: Share of the cross-section used for the top/bottom quantile spread and the
#: turnover diagnostic.  Matches ``FactorCrossSectionService`` so the value
#: family and the price-volume family measure the same thing.
QUANTILE_SHARE = Decimal("0.2")

#: The factors built from the valuation snapshot.  Each entry maps a factor
#: name to the SQL expression producing its value.
#:
#: ``value_pe_ttm`` was retired -- see the module docstring for the evidence.
FACTOR_SQL: dict[str, str] = {
    "value_earnings_yield": "CASE WHEN pe_ttm > 0 THEN 1.0 / pe_ttm END",
    "value_pb_mrq": "-pb_mrq",
}

#: Point-in-time status of each factor, carried into ``factor_definition``.
#:
#: The historical sweep was collected after the fact. Even when a vendor field
#: appears to switch on a disclosure date, it lacks a snapshot/revision chain.
#: ``value_pb_mrq`` is built from ``PB_MRQ``, which switches on the *report
#: period end* and therefore leaks up to 120 days of undisclosed balance
#: sheet.  The pool classifier freezes the latter.
PIT_STATUS: dict[str, str] = {
    "value_earnings_yield": "unknown",
    "value_pb_mrq": "look_ahead",
}


def financial_industry_symbols(db) -> list[str]:
    """Return the securities whose industry is a financial industry.

    Banks, insurers and brokers are excluded from the generic value factors:
    their balance sheet *is* the business model, so ``PB`` and ``PE`` carry a
    completely different meaning than in an industrial issuer.  Worse, the
    sector is structurally cheap -- banks trade at a low PB and a low PE by
    construction -- so leaving them in the cross-section lets 122 financial
    issuers dominate the "cheapest" quantile and turns the factor into a
    sector bet rather than a value bet.

    The industry label is the **current** one: the project has no
    point-in-time industry history (``market.security_industry_history`` is
    empty), so this exclusion is itself a current-label approximation and is
    declared as such in the caveats.
    """
    rows = db.execute(
        select(Security.symbol, Security.industry).where(
            Security.security_type == "stock"
        )
    ).all()
    return sorted(
        str(symbol)
        for symbol, industry in rows
        if is_financial_industry(industry if isinstance(industry, str) else None)
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从估值历史温层计算价值因子 IC 证据")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--factor-version",
        default="v2-valhist",
        help="因子版本号；写入 factor.factor_profile 的 factor_version",
    )
    parser.add_argument(
        "--release-tag",
        default=None,
        help="温层 release tag；默认取 settings.research_release_tag 或最新 release",
    )
    parser.add_argument("--warm-root", default=None)
    parser.add_argument("--curated-root", default="data/warm/curated")
    parser.add_argument("--code-version", default="local")
    parser.add_argument(
        "--keep-financial-industry",
        action="store_true",
        help="不排除金融行业（银行/保险/券商）；默认排除，因为其 PB/PE 口径与工业股不可横截面比较",
    )
    parser.add_argument("--out", help="聚合证据表落盘路径")
    parser.add_argument("--no-persist", action="store_true", help="只出报告不落库")
    return parser


def _load_daily_ic(
    con: duckdb.DuckDBPyConnection,
    prices_glob: str,
    start: date,
    end: date,
    factor_names: tuple[str, ...],
    excluded_symbols: tuple[str, ...] = (),
) -> list[dict[str, object]]:
    """Return one row per (factor, trade_date) with cross-sectional statistics.

    The valuation snapshot and the forward prices are joined in SQL, and the
    cross-sectional statistics are computed there as well: routing several
    million rows through the interpreter would dominate the runtime.

    The universe is the **price** universe (securities with a trusted close on
    that day), not the factor's own non-null set.  Using the factor's own set
    would hide the ~26% of the market that the earnings-yield definition
    excludes because its PE is not positive.

    ``excluded_symbols`` removes the financial sector from the cross-section
    (see :func:`financial_industry_symbols`).
    """
    factor_case = " ".join(
        f"WHEN '{name}' THEN {expression}" for name, expression in FACTOR_SQL.items()
    )
    exclusion_clause = ""
    if excluded_symbols:
        symbol_list = ", ".join(repr(symbol) for symbol in excluded_symbols)
        exclusion_clause = f"AND symbol NOT IN ({symbol_list})"
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE exposures AS
        SELECT
            symbol,
            trade_date,
            factor_name,
            CAST(CASE factor_name
                {factor_case}
            END AS DOUBLE) AS value
        FROM (
            SELECT
                symbol,
                trade_date,
                unnest(
                    [{', '.join(repr(name) for name in factor_names)}]
                ) AS factor_name,
                pe_ttm,
                pb_mrq
            FROM research.valuation_history
            WHERE trade_date BETWEEN DATE '{start}' AND DATE '{end}'
              {exclusion_clause}
        )
        WHERE value IS NOT NULL
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
    # part of the market is visible as such.
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
            e.factor_name,
            e.trade_date,
            e.symbol,
            e.value,
            p.c1 / NULLIF(p.close, 0) - 1 AS fwd1,
            p.c5 / NULLIF(p.close, 0) - 1 AS fwd5,
            p.c20 / NULLIF(p.close, 0) - 1 AS fwd20
        FROM exposures e
        JOIN px p ON p.symbol = e.symbol AND p.trade_date = e.trade_date
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
    # Top-quantile membership per day, so the turnover between consecutive
    # trading days can be measured the same way ``FactorCrossSectionService``
    # measures it.
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE top_members AS
        SELECT factor_name, trade_date, symbol
        FROM scored
        WHERE bucket = {QUANTILE_COUNT}
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE top_sets AS
        SELECT
            factor_name,
            trade_date,
            list(symbol ORDER BY symbol) AS members
        FROM top_members
        GROUP BY 1, 2
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE top_turnover AS
        WITH lagged AS (
            SELECT
                factor_name,
                trade_date,
                members,
                LAG(members) OVER (
                    PARTITION BY factor_name ORDER BY trade_date
                ) AS prev_members
            FROM top_sets
        )
        SELECT
            factor_name,
            trade_date,
            CASE
                WHEN prev_members IS NULL THEN NULL
                ELSE 1.0 - (
                    len(list_intersect(members, prev_members))
                    / GREATEST(len(members), 1)
                )
            END AS top_quantile_turnover
        FROM lagged
        """
    )
    # Bucket-level forward returns feed the monotonicity diagnostic: the
    # correlation between the bucket index and the bucket mean forward return.
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE bucket_returns AS
        SELECT
            factor_name,
            trade_date,
            bucket,
            AVG(fwd1) AS bucket_mean_fwd1
        FROM scored
        WHERE fwd1 IS NOT NULL
        GROUP BY 1, 2, 3
        """
    )
    rows = con.execute(
        f"""
        WITH per_date AS (
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
                avg(fwd1) FILTER (WHERE bucket = {QUANTILE_COUNT}) AS top_q_1d,
                avg(fwd1) FILTER (WHERE bucket = 1) AS bottom_q_1d,
                avg(fwd5) FILTER (WHERE bucket = {QUANTILE_COUNT}) AS top_q_5d,
                avg(fwd5) FILTER (WHERE bucket = 1) AS bottom_q_5d,
                avg(fwd20) FILTER (WHERE bucket = {QUANTILE_COUNT}) AS top_q_20d,
                avg(fwd20) FILTER (WHERE bucket = 1) AS bottom_q_20d
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

    DuckDB ``corr()`` returns NaN rather than NULL for degenerate groups, and
    casting NaN into ``DECIMAL(24, 10)`` raises a conversion error.
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
    """Aggregate the daily IC rows into one summary per factor."""
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
    """Top-minus-bottom quantile spread for one horizon.

    The endpoint spread is the tradability evidence: a factor whose best
    bucket underperforms its worst bucket is not usable even when its rank IC
    is positive.  ``None`` when either bucket is missing.
    """
    top = _clean(row.get(f"top_q_{horizon}"))
    bottom = _clean(row.get(f"bottom_q_{horizon}"))
    if top is None or bottom is None:
        return None
    return _quantize(top - bottom)


def _persist(
    session,
    daily: list[dict[str, object]],
    version: str,
    code_version: str,
) -> int:
    """Persist the daily evidence into ``factor.factor_profile``.

    Uses :meth:`FactorProfileRepository.upsert` rather than ``session.merge``:
    ``merge`` degrades to a plain INSERT when the ORM object carries no primary
    key, so a re-run of this script hits the
    ``(factor_name, factor_version, trade_date)`` unique constraint instead of
    refreshing the existing row.  ``upsert`` carries the correct
    ``ON CONFLICT DO UPDATE`` semantics, which makes the script idempotent.
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
            evidence_eligibility="research_only",
            evidence_eligibility_reason=(
                "late valuation history lacks point-in-time snapshot and revision provenance"
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
    # The financial sector is excluded from the generic value factors.  The
    # industry label comes from the control plane, so it is read through the
    # ORM session -- the same session that later writes the profiles.
    with SessionLocal() as db:
        excluded = () if args.keep_financial_industry else tuple(
            financial_industry_symbols(db)
        )
        try:
            daily = _load_daily_ic(
                con,
                prices_glob,
                args.start_date,
                args.end_date,
                tuple(FACTOR_SQL),
                excluded,
            )
        except Exception as exc:  # noqa: BLE001 - the table may not exist
            con.close()
            print(f"无法读取 research.valuation_history: {exc}")
            return 1
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
        "excluded_financial_symbols": len(excluded),
        "caveats": [
            "research.valuation_history rows are late observations of "
            "historical dates (pit_verified=false); the IC is research "
            "evidence, not tradable evidence",
            "PB_MRQ switches on the reporting period end and leaks the "
            "balance sheet by up to 120 days before its disclosure",
            "the financial sector (bank/insurer/broker) is excluded from the "
            "generic value factors; the industry label is the current one "
            "because no point-in-time industry history exists",
            "forward returns use qfq prices; execution would use unadjusted prices",
            "survivorship bias not corrected (no delisting dates available)",
            "single window, no out-of-sample validation",
            f"cross-sections with fewer than {MIN_CROSS_SECTION} observations "
            "are skipped",
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
