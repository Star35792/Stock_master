"""Shared forward-adjusted (前复权 / QFQ) price reconstruction SQL.

Both the on-the-fly factor pipeline and the materialized curated layer build the
QFQ series from the same definition so the two can never drift. The view joins
raw (unadjusted) daily bars against corporate-action events, compounds the
per-event ratios backward, then normalizes so the most recent bar keeps its raw
price (that is what makes it *forward* adjusted).

Two source modes are supported:

- ``create_qfq_view`` reads daily bars and corporate actions from parquet globs.
  This is the file-lake path used by ``LakeFactorPipeline`` and the offline
  ``build_curated_layer`` script.
- ``create_qfq_view_from_tables`` reads from live DuckDB tables. This is the path
  used when rebuilding ``curated/prices_qfq`` from the ``daily_loop`` after it has
  populated ``market.daily_bar`` and ``raw.corporate_action``.

The paths are substituted rather than parameterized where DuckDB's
``read_parquet`` glob argument must be a literal inside ``CREATE VIEW``.
"""

from __future__ import annotations

import duckdb


def create_qfq_view(
    connection: duckdb.DuckDBPyConnection,
    *,
    daily_glob: str,
    action_glob: str,
    view_name: str = "qfq_bars",
) -> None:
    """Register a TEMP VIEW exposing forward-adjusted OHLCV from parquet globs."""
    daily_source = (
        f"SELECT symbol, trade_date, open, high, low, close, volume, amount, "
        f"CAST(NULL AS DECIMAL(12,6)) AS turnover_rate "
        f"FROM read_parquet({_sql_literal(daily_glob)}, hive_partitioning=true)"
    )
    action_source = (
        "SELECT symbol, ex_date, dividend_per_share, per_share_bonus, "
        "allotment_ratio, allotment_price "
        f"FROM read_parquet({_sql_literal(action_glob)}, hive_partitioning=true)"
    )
    _create_view(connection, view_name, daily_source, action_source)


def create_qfq_view_from_tables(
    connection: duckdb.DuckDBPyConnection,
    *,
    daily_table: str = "market.daily_bar",
    action_table: str = "raw.corporate_action",
    view_name: str = "qfq_bars",
) -> None:
    """Register a TEMP VIEW exposing forward-adjusted OHLCV from live tables.

    ``daily_table`` must expose ``symbol, trade_date, open, high, low, close,
    volume, amount, turnover_rate`` and should already be filtered to the desired
    ``adjust_type`` and ``quality_status`` by the caller (typically
    ``adjust_type='none'`` and ``quality_status='trusted'``).  ``action_table``
    must expose ``symbol, ex_date, dividend_per_share, per_share_bonus,
    allotment_ratio, allotment_price``.
    """
    daily_source = (
        f"SELECT symbol, trade_date, open, high, low, close, volume, amount, "
        f"turnover_rate FROM {daily_table}"
    )
    action_source = (
        "SELECT symbol, ex_date, dividend_per_share, per_share_bonus, "
        f"allotment_ratio, allotment_price FROM {action_table}"
    )
    _create_view(connection, view_name, daily_source, action_source)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _create_view(
    connection: duckdb.DuckDBPyConnection,
    view_name: str,
    daily_source: str,
    action_source: str,
) -> None:
    sql = (
        _QFQ_VIEW_SQL.replace("{view_name}", view_name)
        .replace("{daily_source}", daily_source)
        .replace("{action_source}", action_source)
    )
    connection.execute(sql)


_QFQ_VIEW_SQL = """
CREATE OR REPLACE TEMP VIEW {view_name} AS
WITH daily AS (
    SELECT symbol, trade_date, open, high, low, close, volume, amount, turnover_rate
    FROM ({daily_source})
), effective_events AS (
    SELECT
        e.symbol,
        next_bar.trade_date AS effective_date,
        (prev_bar.close * (1 + coalesce(e.per_share_bonus, 0)
                               + coalesce(e.allotment_ratio, 0)))
        / nullif(prev_bar.close - coalesce(e.dividend_per_share, 0)
                 + coalesce(e.allotment_ratio, 0) * coalesce(e.allotment_price, 0), 0)
            AS ratio
    FROM ({action_source}) e
    ASOF JOIN daily next_bar
      ON e.symbol = next_bar.symbol AND e.ex_date <= next_bar.trade_date
    ASOF LEFT JOIN daily prev_bar
      ON e.symbol = prev_bar.symbol AND next_bar.trade_date > prev_bar.trade_date
), day_ratios AS (
    SELECT symbol, effective_date, exp(sum(ln(ratio))) AS ratio
    FROM effective_events
    WHERE ratio > 0
    GROUP BY symbol, effective_date
), cumulative AS (
    SELECT d.*,
           exp(sum(ln(coalesce(r.ratio, 1))) OVER (
               PARTITION BY d.symbol ORDER BY d.trade_date
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)) AS backward_factor
    FROM daily d
    LEFT JOIN day_ratios r ON r.symbol = d.symbol AND r.effective_date = d.trade_date
), normalized AS (
    SELECT *, backward_factor / last_value(backward_factor) OVER (
        PARTITION BY symbol ORDER BY trade_date
        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS qfq_factor
    FROM cumulative
)
SELECT symbol, trade_date,
       open * qfq_factor AS open_qfq, high * qfq_factor AS high_qfq,
       low * qfq_factor AS low_qfq, close * qfq_factor AS close_qfq,
       volume, amount, turnover_rate, qfq_factor
FROM normalized
"""
