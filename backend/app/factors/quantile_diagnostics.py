"""DuckDB helpers for tie-aware cross-sectional factor diagnostics.

The warm-layer IC scripts share this preparation step.  Keeping it here avoids
letting their Rank IC, endpoint, monotonicity, and turnover definitions drift.
The input table must expose ``factor_name``, ``trade_date``, ``symbol``,
``value``, and ``fwd1``/``fwd5``/``fwd20``.
"""

from __future__ import annotations

import re
from decimal import Decimal

import duckdb

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def prepare_tie_aware_quantiles(
    con: duckdb.DuckDBPyConnection,
    *,
    source_table: str = "joined",
    quantile_count: int = 5,
    endpoint_share: Decimal = Decimal("0.2"),
) -> None:
    """Create ``scored``, ``top_turnover``, and ``bucket_returns`` temp tables.

    Equal factor values always receive the same average rank and monotonicity
    bucket.  Endpoint boundaries use the nominal tail size but include the
    complete value group tied at the boundary.  Top-set turnover is symmetric,
    so expanding and contracting a boundary tie group have the same cost.
    """
    if not _IDENTIFIER.fullmatch(source_table):
        raise ValueError("source_table must be a simple SQL identifier")
    if quantile_count < 2:
        raise ValueError("quantile_count must be >= 2")
    if endpoint_share <= 0 or endpoint_share > Decimal("0.5"):
        raise ValueError("endpoint_share must be in (0, 0.5]")

    share_sql = str(endpoint_share)
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE quantile_boundaries AS
        WITH grouped AS (
            SELECT
                factor_name,
                trade_date,
                list(value ORDER BY value) AS ordered_values,
                list(value ORDER BY value) FILTER (
                    WHERE fwd1 IS NOT NULL
                ) AS ordered_values_1d,
                list(value ORDER BY value) FILTER (
                    WHERE fwd5 IS NOT NULL
                ) AS ordered_values_5d,
                list(value ORDER BY value) FILTER (
                    WHERE fwd20 IS NOT NULL
                ) AS ordered_values_20d,
                greatest(
                    1,
                    CAST(ceil(count(*) * CAST('{share_sql}' AS DECIMAL(10, 9))) AS BIGINT)
                ) AS endpoint_count,
                greatest(
                    1,
                    CAST(ceil(count(fwd1) * CAST('{share_sql}' AS DECIMAL(10, 9))) AS BIGINT)
                ) AS endpoint_count_1d,
                greatest(
                    1,
                    CAST(ceil(count(fwd5) * CAST('{share_sql}' AS DECIMAL(10, 9))) AS BIGINT)
                ) AS endpoint_count_5d,
                greatest(
                    1,
                    CAST(ceil(count(fwd20) * CAST('{share_sql}' AS DECIMAL(10, 9))) AS BIGINT)
                ) AS endpoint_count_20d
            FROM {source_table}
            GROUP BY 1, 2
        )
        SELECT
            factor_name,
            trade_date,
            list_extract(ordered_values, endpoint_count) AS bottom_boundary,
            list_extract(
                ordered_values,
                len(ordered_values) - endpoint_count + 1
            ) AS top_boundary,
            list_extract(ordered_values_1d, endpoint_count_1d) AS bottom_boundary_1d,
            list_extract(
                ordered_values_1d,
                len(ordered_values_1d) - endpoint_count_1d + 1
            ) AS top_boundary_1d,
            list_extract(ordered_values_5d, endpoint_count_5d) AS bottom_boundary_5d,
            list_extract(
                ordered_values_5d,
                len(ordered_values_5d) - endpoint_count_5d + 1
            ) AS top_boundary_5d,
            list_extract(ordered_values_20d, endpoint_count_20d) AS bottom_boundary_20d,
            list_extract(
                ordered_values_20d,
                len(ordered_values_20d) - endpoint_count_20d + 1
            ) AS top_boundary_20d
        FROM grouped
        """
    )
    con.execute(
        f"""
        CREATE OR REPLACE TEMP TABLE scored AS
        WITH ranked AS (
            SELECT
                *,
                CASE WHEN fwd1 IS NOT NULL THEN rank() OVER value1_order END
                    AS value_rank_start_1d,
                CASE WHEN fwd1 IS NOT NULL THEN count(*) OVER value1_tie END
                    AS value_tie_count_1d,
                CASE WHEN fwd5 IS NOT NULL THEN rank() OVER value5_order END
                    AS value_rank_start_5d,
                CASE WHEN fwd5 IS NOT NULL THEN count(*) OVER value5_tie END
                    AS value_tie_count_5d,
                CASE WHEN fwd20 IS NOT NULL THEN rank() OVER value20_order END
                    AS value_rank_start_20d,
                CASE WHEN fwd20 IS NOT NULL THEN count(*) OVER value20_tie END
                    AS value_tie_count_20d,
                count(fwd1) OVER factor_day AS cross_section_count_1d,
                CASE WHEN fwd1 IS NOT NULL THEN
                    rank() OVER fwd1_order
                        + (count(*) OVER fwd1_tie - 1) / 2.0
                END AS rk_fwd1,
                CASE WHEN fwd5 IS NOT NULL THEN
                    rank() OVER fwd5_order
                        + (count(*) OVER fwd5_tie - 1) / 2.0
                END AS rk_fwd5,
                CASE WHEN fwd20 IS NOT NULL THEN
                    rank() OVER fwd20_order
                        + (count(*) OVER fwd20_tie - 1) / 2.0
                END AS rk_fwd20
            FROM {source_table}
            WINDOW
                factor_day AS (PARTITION BY factor_name, trade_date),
                value1_order AS (
                    factor_day ORDER BY CASE WHEN fwd1 IS NOT NULL THEN value END
                ),
                value1_tie AS (
                    PARTITION BY factor_name, trade_date,
                    CASE WHEN fwd1 IS NOT NULL THEN value END
                ),
                value5_order AS (
                    factor_day ORDER BY CASE WHEN fwd5 IS NOT NULL THEN value END
                ),
                value5_tie AS (
                    PARTITION BY factor_name, trade_date,
                    CASE WHEN fwd5 IS NOT NULL THEN value END
                ),
                value20_order AS (
                    factor_day ORDER BY CASE WHEN fwd20 IS NOT NULL THEN value END
                ),
                value20_tie AS (
                    PARTITION BY factor_name, trade_date,
                    CASE WHEN fwd20 IS NOT NULL THEN value END
                ),
                fwd1_order AS (factor_day ORDER BY fwd1),
                fwd1_tie AS (PARTITION BY factor_name, trade_date, fwd1),
                fwd5_order AS (factor_day ORDER BY fwd5),
                fwd5_tie AS (PARTITION BY factor_name, trade_date, fwd5),
                fwd20_order AS (factor_day ORDER BY fwd20),
                fwd20_tie AS (PARTITION BY factor_name, trade_date, fwd20)
        )
        SELECT
            ranked.* EXCLUDE (
                value_rank_start_1d,
                value_tie_count_1d,
                value_rank_start_5d,
                value_tie_count_5d,
                value_rank_start_20d,
                value_tie_count_20d,
                cross_section_count_1d
            ),
            value_rank_start_1d + (value_tie_count_1d - 1) / 2.0 AS rk_value_1d,
            value_rank_start_5d + (value_tie_count_5d - 1) / 2.0 AS rk_value_5d,
            value_rank_start_20d + (value_tie_count_20d - 1) / 2.0 AS rk_value_20d,
            least(
                {quantile_count},
                CAST(
                    floor(
                        (
                            value_rank_start_1d - 1
                            + (value_tie_count_1d - 1) / 2.0
                        ) * {quantile_count} / cross_section_count_1d
                    ) AS INTEGER
                ) + 1
            ) AS bucket,
            boundaries.bottom_boundary,
            boundaries.top_boundary,
            boundaries.bottom_boundary_1d,
            boundaries.top_boundary_1d,
            boundaries.bottom_boundary_5d,
            boundaries.top_boundary_5d,
            boundaries.bottom_boundary_20d,
            boundaries.top_boundary_20d
        FROM ranked
        JOIN quantile_boundaries AS boundaries
          USING (factor_name, trade_date)
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE top_sets AS
        SELECT
            factor_name,
            trade_date,
            list(symbol ORDER BY symbol) FILTER (
                WHERE value >= top_boundary
            ) AS members
        FROM scored
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
                lag(members) OVER (
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
                    2.0 * len(list_intersect(members, prev_members))
                    / greatest(len(members) + len(prev_members), 1)
                )
            END AS top_quantile_turnover
        FROM lagged
        """
    )
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE bucket_returns AS
        SELECT
            factor_name,
            trade_date,
            bucket,
            avg(fwd1) AS bucket_mean_fwd1
        FROM scored
        WHERE fwd1 IS NOT NULL
        GROUP BY 1, 2, 3
        """
    )
