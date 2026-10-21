"""Warm-layer storage abstraction.

Phase 2 of the storage split moves the four largest analytical tables out of
``stock_master.duckdb`` into partitioned Parquet files under
``data/warm/exported/<release_tag>/``.  ``WarmLayerStore`` exposes those
Parquet files through an in-memory DuckDB connection that keeps the original
``schema.table`` names usable, so existing SQL and research scripts keep working
without rewriting every query.

The connection layout is:

* ``:memory:`` database owns lightweight VIEWS named ``market.daily_bar``,
  ``market.adjustment_factor``, ``raw.daily_bar``, ``factor.factor_value`` that
  point at the exported Parquet files.
* The control-plane DuckDB file is ATTACHed as ``ctrl``.  All remaining tables
  (``market.security``, ``market.trading_calendar``, ``metadata.job_log``,
  ``strategy.backtest_run``, etc.) are exposed as local VIEWS that delegate to
  ``ctrl.<schema>.<table>``.
* A convenience view ``market.prices_qfq`` is also created pointing at
  ``data/warm/curated/prices_qfq`` so research scripts can read pre-computed
  forward-adjusted OHLCV directly.

Writes still go through the control-plane connection / SQLAlchemy session; the
warm layer is read-only by design.  This lets the main DuckDB file shrink once
historical rows are removed, while daily incremental writes stay on the familiar
ORM path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from app.config.settings import settings

WARM_TABLES: frozenset[tuple[str, str]] = frozenset(
    {
        ("market", "daily_bar"),
        ("market", "adjustment_factor"),
        ("raw", "daily_bar"),
        ("factor", "factor_value"),
        ("raw", "corporate_action"),
        ("fundamental", "financial_statement"),
        ("raw", "financial_payload"),
    }
)

# Date column used to merge the Parquet warm layer with the control-plane hot
# window for each warm table.  The cutoff is ``MAX(date_col)`` in the exported
# Parquet; rows in the control plane with a later date are UNIONed on top.
WARM_TABLE_DATE_COLUMNS: dict[tuple[str, str], str] = {
    ("market", "daily_bar"): "trade_date",
    ("market", "adjustment_factor"): "trade_date",
    ("raw", "daily_bar"): "trade_date",
    ("factor", "factor_value"): "trade_date",
    ("raw", "corporate_action"): "ex_date",
    ("fundamental", "financial_statement"): "report_date",
    ("raw", "financial_payload"): "report_date",
}

# Schemas that live in the control-plane DuckDB file and must be delegated to
# the ATTACHed database.  We create local views for every table in these schemas
# so that ``schema.table`` references resolve without a ``ctrl.`` prefix.
CONTROL_SCHEMAS: tuple[str, ...] = (
    "market",
    "raw",
    "factor",
    "fundamental",
    "strategy",
    "portfolio",
    "risk",
    "trade",
    "lake",
    "metadata",
)

# Schemas for which we build parquet-backed warm views.  Other schemas are fully
# delegated to the control plane.
WARM_SCHEMAS: tuple[str, ...] = ("market", "raw", "factor", "fundamental")


class WarmLayerStore:
    """Access exported Parquet warm layers as if they were local DuckDB tables."""

    def __init__(self, warm_root: str | Path | None = None) -> None:
        self.warm_root = Path(warm_root or settings.warm_layer_root).resolve()

    def _exported_root(self) -> Path:
        return self.warm_root / "exported"

    @property
    def latest_release_tag(self) -> str | None:
        """Return the most recent release tag directory name, or ``None``."""
        exported = self._exported_root()
        if not exported.exists():
            return None
        candidates = [
            p.name
            for p in exported.iterdir()
            if p.is_dir() and (p / "export_summary.json").exists()
        ]
        if not candidates:
            return None
        # Tags are YYYYMMDD-HHMMSS; lexicographic sort == chronological sort.
        return sorted(candidates)[-1]

    def release_path(self, release_tag: str | None = None) -> Path:
        """Return the path to a specific release (latest if not given)."""
        tag = release_tag or self.latest_release_tag
        if tag is None:
            raise ValueError(
                f"no warm-layer release found under {self._exported_root()}"
            )
        return self._exported_root() / tag

    def table_glob(
        self,
        schema: str,
        table: str,
        release_tag: str | None = None,
    ) -> Path:
        """Return the glob path for a warm table's parquet partitions."""
        return self.release_path(release_tag) / schema / table

    def prices_qfq_glob(self) -> Path:
        """Return the glob path for the curated forward-adjusted price layer.

        If the active ``prices_qfq`` directory does not exist (e.g. because a
        rebuild left it as a timestamped backup), return the most recent backup.
        """
        lake_root = Path(settings.data_lake_root).resolve()
        curated = lake_root / "curated"
        primary = curated / "prices_qfq"
        if primary.exists():
            return primary
        backups = sorted(
            p for p in curated.glob("prices_qfq.backup-*") if p.is_dir()
        )
        if backups:
            return backups[-1]
        return primary

    def connect(
        self,
        control_db_path: str | Path | None = None,
        *,
        release_tag: str | None = None,
        read_only: bool = True,
    ) -> duckdb.DuckDBPyConnection:
        """Open an in-memory DuckDB connection backed by warm parquet + control plane.

        The returned connection is owned by the caller and must be closed with
        ``.close()``.  For context-manager safety you can wrap it in
        ``duckdb.connect`` style code.
        """
        control_db = Path(control_db_path or settings.metadata_db_path).resolve()
        if not control_db.exists():
            raise FileNotFoundError(f"control-plane database not found: {control_db}")

        _ = self.release_path(release_tag)
        con = duckdb.connect(":memory:")
        con.execute("SET threads = 1")

        # Attach the control-plane database.  Use READ_ONLY when possible to avoid
        # any accidental writes through the warm connection.
        attach_options = "(READ_ONLY)" if read_only else ""
        con.execute(f"ATTACH '{control_db}' AS ctrl {attach_options}")

        # For each control schema, create a local schema and delegate every table.
        # Warm tables become parquet-backed views merged with the hot rows still
        # resident in the control-plane DB; everything else delegates to the
        # attached control plane.
        #
        # The merge uses the parquet max(trade_date) as a cutoff so that rows
        # already exported to Parquet are not duplicated from the control plane.
        # This lets the control-plane DB shrink to a hot-window while readers
        # still see the complete time series.
        for schema in CONTROL_SCHEMAS:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            tables = con.execute(
                f"""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_catalog = 'ctrl'
                  AND table_schema = '{schema}'
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            ).fetchall()
            for (table_name,) in tables:
                if (schema, table_name) in WARM_TABLES:
                    glob = self.table_glob(schema, table_name, release_tag)
                    parquet_glob = f"{glob.as_posix()}/**/*.parquet"
                    # Parquet files include hive partition columns (trade_year,
                    # trade_month) that the control-plane table does not have.
                    # Build an explicit column list so UNION ALL stays aligned.
                    col_rows = con.execute(
                        f"""
                        DESCRIBE
                        SELECT *
                        FROM read_parquet('{parquet_glob}', hive_partitioning=true)
                        """
                    ).fetchall()
                    partition_cols = {"trade_year", "trade_month"}
                    columns = [c[0] for c in col_rows if c[0] not in partition_cols]
                    if not columns:
                        raise ValueError(
                            f"no columns found for warm table {schema}.{table_name}"
                        )
                    col_sql = ", ".join(columns)

                    date_col = WARM_TABLE_DATE_COLUMNS[(schema, table_name)]
                    parquet_max = con.execute(
                        f"""
                        SELECT MAX({date_col})
                        FROM read_parquet('{parquet_glob}', hive_partitioning=true)
                        """
                    ).fetchone()[0]
                    if parquet_max is not None:
                        con.execute(
                            f"""
                            CREATE OR REPLACE VIEW {schema}.{table_name} AS
                            SELECT {col_sql}
                            FROM read_parquet('{parquet_glob}', hive_partitioning=true)
                            UNION ALL
                            SELECT {col_sql} FROM ctrl.{schema}.{table_name}
                            WHERE {date_col} > '{parquet_max}'
                            """
                        )
                    else:
                        con.execute(
                            f"""
                            CREATE OR REPLACE VIEW {schema}.{table_name} AS
                            SELECT {col_sql} FROM ctrl.{schema}.{table_name}
                            """
                        )
                else:
                    con.execute(
                        f"""
                        CREATE OR REPLACE VIEW {schema}.{table_name} AS
                        SELECT * FROM ctrl.{schema}.{table_name}
                        """
                    )

        # Convenience view for the pre-computed forward-adjusted price layer.
        # This exists alongside ``market.daily_bar`` (which only stores
        # ``adjust_type='none'`` rows in the current design).
        prices_qfq = self.prices_qfq_glob()
        if prices_qfq.exists():
            con.execute(
                f"""
                CREATE OR REPLACE VIEW market.prices_qfq AS
                SELECT *
                FROM read_parquet('{prices_qfq.as_posix()}/**/*.parquet', hive_partitioning=true)
                """
            )

        return con

    def list_releases(self) -> list[dict[str, Any]]:
        """List available warm-layer releases with basic metadata."""
        exported = self._exported_root()
        if not exported.exists():
            return []
        releases: list[dict[str, Any]] = []
        for path in sorted(exported.iterdir()):
            summary = path / "export_summary.json"
            if not summary.exists():
                continue
            releases.append(
                {
                    "release_tag": path.name,
                    "path": str(path),
                    "summary_path": str(summary),
                }
            )
        return releases
