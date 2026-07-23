"""Shared forward-adjusted (前复权 / QFQ) price reconstruction SQL.

Both the on-the-fly factor pipeline and the materialized curated layer build the
QFQ series from the same definition so the two can never drift. The view joins
raw (unadjusted) daily bars against corporate-action events, compounds the
per-event ratios backward, then normalizes so the most recent bar keeps its raw
price (that is what makes it *forward* adjusted).

The paths are substituted rather than parameterized because DuckDB's
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
    """Register a TEMP VIEW exposing forward-adjusted OHLCV for every symbol."""
    sql = (
        _QFQ_VIEW_SQL.replace("{view_name}", view_name)
        .replace("{daily_path}", _sql_literal(daily_glob))
        .replace("{action_path}", _sql_literal(action_glob))
    )
    connection.execute(sql)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_QFQ_VIEW_SQL = """
CREATE TEMP VIEW {view_name} AS
WITH daily AS (
    SELECT symbol, trade_date, open, high, low, close, volume, amount
    FROM read_parquet({daily_path}, hive_partitioning=true)
), effective_events AS (
    SELECT
        e.symbol,
        next_bar.trade_date AS effective_date,
        (prev_bar.close * (1 + coalesce(e.per_share_bonus, 0)
                               + coalesce(e.allotment_ratio, 0)))
        / nullif(prev_bar.close - coalesce(e.dividend_per_share, 0)
                 + coalesce(e.allotment_ratio, 0) * coalesce(e.allotment_price, 0), 0)
            AS ratio
    FROM read_parquet({action_path}, hive_partitioning=true) e
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
       volume, amount, qfq_factor
FROM normalized
"""
