"""Curated analysis layer for the data lake.

The raw layer holds vendor dumps verbatim. The curated layer holds
analysis-ready datasets, every one aligned to the ``(symbol, trade_date)`` grid
so that every factor family reads the same shape. Two materializers live here:

- ``build_prices_qfq`` — forward-adjusted OHLCV computed *once* and written to
  ``curated/prices_qfq`` partitioned by year/month. This replaces the per-run
  QFQ recompute that every price-based factor used to pay for.
- ``build_fundamentals_pit`` — financial statements flattened from their JSON
  ``metrics`` blob into typed columns, keyed by ``report_date`` so factors can
  do a point-in-time ``ASOF JOIN ... report_date < trade_date`` without a JSON
  full-scan.

All timestamp handling stays UTC-agnostic: source dumps already land as DATE
columns in the raw layer, so no timezone conversion happens here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from app.lake.data_lake import DataLake, _compact_partitions
from app.lake.qfq import create_qfq_view
from app.lake.security_type import security_type_case_sql

# Line items pulled out of the raw ``metrics`` JSON into typed DECIMAL columns.
# Each column coalesces across the vendor's alias spellings, mirroring the alias
# tuples in app/services/financial_factors.py so the flattened table is a
# drop-in for the JSON lookups it replaces. Keyed by target statement_type.
_PIT_COLUMNS: dict[str, dict[str, tuple[str, ...]]] = {
    "balance": {
        "total_assets": ("total_assets", "total_asset", "assets_total"),
        "total_liabilities": ("total_liabilities", "total_liability", "liabilities_total"),
        "total_equity": (
            "total_equity",
            "total_shareholders_equity",
            "total_equity_attributable_to_parent",
            "shareholders_equity",
        ),
        "current_assets": ("current_assets", "total_current_assets"),
        "current_liabilities": ("current_liabilities", "total_current_liabilities"),
        "accounts_receivable": (
            "accounts_receivable",
            "notes_and_accounts_receivable",
            "receivable",
        ),
        "inventory": ("inventory", "inventories"),
    },
    "income": {
        "operating_income": ("operating_revenue", "total_revenue", "revenue", "operating_income"),
        "operating_cost": ("operating_cost", "operating_costs", "cost_of_revenue"),
        "operating_profit": ("operating_profit", "profit_from_operations"),
        "net_profit": (
            "net_profit",
            "net_income",
            "net_profit_attributable_to_parent",
            "parent_net_profit",
        ),
        "interest_expense": ("interest_expense", "finance_expense", "financial_expense"),
    },
    "cash_flow": {
        "operating_cash_flow": (
            "operating_cash_flow",
            "net_operating_cash_flow",
            "cash_flow_from_operations",
        ),
    },
}


@dataclass(frozen=True)
class CuratedDatasetResult:
    dataset: str
    path: str
    row_count: int
    min_date: str | None
    max_date: str | None
    created_at: str


class CuratedLayer:
    """Builds analysis-ready datasets from the raw lake and vendor sources."""

    def __init__(self, lake: DataLake) -> None:
        self.lake = lake
        self.root = lake.root / "curated"

    def build_prices_qfq(self) -> CuratedDatasetResult:
        """Materialize forward-adjusted OHLCV from the latest raw daily bars.

        The QFQ series is expensive to derive (it compounds every corporate
        action per symbol), so it is computed once here rather than inside each
        factor run. Output is partitioned by year/month and sorted by
        (symbol, trade_date) to serve both per-symbol time series and
        per-date cross sections.
        """
        target = self.root / "prices_qfq"
        if target.exists():
            raise FileExistsError(
                f"curated dataset already exists, drop it first: {target}"
            )
        daily = Path(self.lake.latest("daily_bar").path) / "**" / "*.parquet"
        actions = Path(self.lake.latest("corporate_action").path) / "**" / "*.parquet"
        target.mkdir(parents=True, exist_ok=False)

        connection = duckdb.connect()
        try:
            connection.execute("SET threads = 1")
            create_qfq_view(
                connection, daily_glob=str(daily), action_glob=str(actions)
            )
            stats = connection.execute(
                "SELECT count(*), min(trade_date), max(trade_date) FROM qfq_bars"
            ).fetchone()
            connection.execute(
                f"""
                COPY (
                    SELECT
                        symbol,
                        {security_type_case_sql("symbol")} AS security_type,
                        trade_date,
                        open_qfq AS open, high_qfq AS high,
                        low_qfq AS low, close_qfq AS close,
                        volume, amount,
                        CAST(NULL AS DECIMAL(12,6)) AS turnover_rate,
                        qfq_factor,
                        year(trade_date) AS year, month(trade_date) AS month
                    FROM qfq_bars
                    ORDER BY symbol, trade_date
                ) TO ? (
                    FORMAT PARQUET, COMPRESSION ZSTD,
                    PARTITION_BY (year, month), PER_THREAD_OUTPUT false,
                    ROW_GROUP_SIZE 100000
                )
                """,
                [str(target)],
            )
        except Exception:
            _cleanup(target)
            raise
        finally:
            connection.close()
        _compact_partitions(target)
        return _result("prices_qfq", target, stats)

    def build_fundamentals_pit(self, source: str | Path) -> CuratedDatasetResult:
        """Flatten financial statements into typed, point-in-time columns.

        ``source`` is a Parquet file/glob with one row per
        (symbol, statement_type, period) carrying a JSON ``metrics`` blob and a
        ``report_date`` (disclosure date). Rows are pivoted so each symbol×period
        yields one wide row with every line item as a DECIMAL column, keyed by
        ``report_date`` for ASOF joins against the trade-date grid.
        """
        target = self.root / "fundamentals_pit"
        if target.exists():
            raise FileExistsError(
                f"curated dataset already exists, drop it first: {target}"
            )
        source_glob = str(Path(source).resolve())
        target.mkdir(parents=True, exist_ok=False)

        connection = duckdb.connect()
        try:
            connection.execute("SET threads = 1")
            select_sql = _fundamentals_pit_select(source_glob)
            stats = connection.execute(
                f"SELECT count(*), min(report_date), max(report_date) FROM ({select_sql})"
            ).fetchone()
            connection.execute(
                f"""
                COPY (
                    SELECT *, year(report_date) AS year FROM ({select_sql})
                    ORDER BY symbol, report_date
                ) TO ? (
                    FORMAT PARQUET, COMPRESSION ZSTD,
                    PARTITION_BY (year), PER_THREAD_OUTPUT false,
                    ROW_GROUP_SIZE 100000
                )
                """,
                [str(target)],
            )
        except Exception:
            _cleanup(target)
            raise
        finally:
            connection.close()
        _compact_partitions(target)
        return _result("fundamentals_pit", target, stats)


def _fundamentals_pit_select(source_glob: str) -> str:
    """Build the SELECT that pivots JSON metrics into typed columns.

    Each target column is ``max(CASE WHEN statement_type = ...
    THEN metrics->>alias END)`` coalesced across aliases, grouped to one row per
    (symbol, report_date, period_end, fiscal_year, fiscal_period).
    """
    projections: list[str] = []
    for statement_type, columns in _PIT_COLUMNS.items():
        for column, aliases in columns.items():
            extracts = [
                f"try_cast(nullif(metrics->>'{alias}', '') AS DECIMAL(24,4))"
                for alias in aliases
            ]
            coalesced = f"coalesce({', '.join(extracts)})"
            projections.append(
                f"max(CASE WHEN statement_type = '{statement_type}' "
                f"THEN {coalesced} END) AS {column}"
            )
    projection_sql = ",\n            ".join(projections)
    literal = "'" + source_glob.replace("'", "''") + "'"
    return f"""
        SELECT
            symbol,
            report_date,
            period_end,
            fiscal_year,
            fiscal_period,
            {projection_sql}
        FROM read_parquet({literal}, hive_partitioning=true)
        GROUP BY symbol, report_date, period_end, fiscal_year, fiscal_period
    """


def _cleanup(target: Path) -> None:
    if not target.exists():
        return
    for path in sorted(target.rglob("*"), reverse=True):
        if path.is_file():
            path.unlink()
        else:
            path.rmdir()
    target.rmdir()


def _result(dataset: str, target: Path, stats: tuple) -> CuratedDatasetResult:
    return CuratedDatasetResult(
        dataset=dataset,
        path=str(target.resolve()),
        row_count=int(stats[0]),
        min_date=stats[1].isoformat() if stats[1] else None,
        max_date=stats[2].isoformat() if stats[2] else None,
        created_at=datetime.now(UTC).isoformat(),
    )
