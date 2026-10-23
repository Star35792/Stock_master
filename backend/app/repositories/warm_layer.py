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

import json
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

    def __init__(
        self,
        warm_root: str | Path | None = None,
        *,
        base_release_tag: str | None = None,
    ) -> None:
        self.warm_root = Path(warm_root or settings.warm_layer_root).resolve()
        # Optional parent release.  A release may only carry the tables it
        # actually changed (e.g. a factor backfill touching ``factor/
        # factor_value``); every other table resolves through this fallback.
        # Without it, every incremental write would have to copy ~700 MB of
        # Parquet just to publish ten new files.
        self.base_release_tag = base_release_tag

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

    def base_release_tag_for(self, release_tag: str) -> str | None:
        """Read the parent release recorded in a release's ``export_summary.json``.

        Partial releases (e.g. a factor backfill that only publishes
        ``factor/factor_value``) record their parent so readers automatically
        inherit every table and every date they did not publish.
        """
        summary = self.release_path(release_tag) / "export_summary.json"
        if not summary.exists():
            return None
        try:
            payload = json.loads(summary.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        parent = payload.get("base_release_tag")
        return parent if isinstance(parent, str) and parent else None

    def table_globs(
        self,
        schema: str,
        table: str,
        release_tag: str | None = None,
    ) -> list[Path]:
        """Return the glob path for a warm table's parquet partitions.

        Tables absent from the requested release fall back to
        ``base_release_tag`` when one is configured, so a release can carry
        only the tables it changed.
        """
        tag = release_tag or self.latest_release_tag
        if tag is None:
            raise ValueError(
                f"no warm-layer release found under {self._exported_root()}"
            )
        # Also from a second (child) release then the parent is consulted too, so a
        # release carrying only ``factor/factor_value`` still exposes every other
        # table and every untouched date.
        globs: list[Path] = []
        seen: set[Path] = set()
        for candidate in (tag, self.base_release_tag):
            if not candidate:
                continue
            path = self._exported_root() / candidate / schema / table
            if path.exists() and path not in seen:
                seen.add(path)
                globs.append(path)
        if not globs:
            # Fall through to the primary location so the caller gets a clear
            # parquet-not-found error instead of an empty union.
            return [self._exported_root() / (tag or "") / schema / table]
        return globs

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
        attach_control: bool = True,
    ) -> duckdb.DuckDBPyConnection:
        """Open a DuckDB connection backed by warm parquet (+ optional control plane).

        ``attach_control=False`` produces a pure Parquet connection that never
        opens the control-plane file.  That matters because DuckDB refuses to
        attach the same file twice once another connection holds it read-write
        ("Unique file handle conflict").  Research reads therefore run against
        Parquet only, while the few control-plane tables that are authoritative
        and not slimmed (``market.corporate_action``, ``market.trading_calendar``)
        are read through the ORM session that already owns the write handle.
        """
        control_db = Path(control_db_path or settings.metadata_db_path).resolve()
        if attach_control and not control_db.exists():
            raise FileNotFoundError(f"control-plane database not found: {control_db}")

        _ = self.release_path(release_tag)
        con = duckdb.connect(":memory:")
        con.execute("SET threads = 1")

        # Attach the control-plane database READ_ONLY when present so this
        # connection can never write through the warm path.
        if attach_control:
            attach_options = "(READ_ONLY)" if read_only else ""
            con.execute(f"ATTACH '{control_db}' AS ctrl {attach_options}")

        # Resolve which (schema, table) pairs this connection exposes.
        if attach_control:
            table_rows: list[tuple[str, str]] = []
            for schema in CONTROL_SCHEMAS:
                con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
                rows = con.execute(
                    f"""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_catalog = 'ctrl'
                      AND table_schema = '{schema}'
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                ).fetchall()
                table_rows.extend((schema, row[0]) for row in rows)
        else:
            # Parquet-only mode: expose warm tables exclusively.  Control-plane
            # facts must be read through the caller's own handle.
            table_rows = sorted(WARM_TABLES)
            for schema in WARM_SCHEMAS:
                con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

        for schema, table_name in table_rows:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            if (schema, table_name) not in WARM_TABLES:
                con.execute(
                    f"""
                    CREATE OR REPLACE VIEW {schema}.{table_name} AS
                    SELECT * FROM ctrl.{schema}.{table_name}
                    """
                )
                continue

            globs = self.table_globs(schema, table_name, release_tag)
            parquet_globs = [f"{g.as_posix()}/**/*.parquet" for g in globs]
            # Parquet files include hive partition columns (trade_year,
            # trade_month) that the control-plane table does not have.  Build an
            # explicit column list so UNION ALL stays aligned.
            col_rows = con.execute(
                f"""
                DESCRIBE
                SELECT *
                FROM read_parquet('{parquet_globs[0]}', hive_partitioning=true)
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
            parquet_selects = " UNION ALL ".join(
                f"SELECT {col_sql} FROM read_parquet('{pattern}', "
                "hive_partitioning=true)"
                for pattern in parquet_globs
            )
            if attach_control:
                parquet_max = con.execute(
                    f"SELECT MAX({date_col}) FROM ({parquet_selects})"
                ).fetchone()[0]
                if parquet_max is not None:
                    con.execute(
                        f"""
                        CREATE OR REPLACE VIEW {schema}.{table_name} AS
                        {parquet_selects}
                        UNION ALL
                        SELECT {col_sql} FROM ctrl.{schema}.{table_name}
                        WHERE {date_col} > '{parquet_max}'
                        """
                    )
                    continue
                con.execute(
                    f"""
                    CREATE OR REPLACE VIEW {schema}.{table_name} AS
                    SELECT {col_sql} FROM ctrl.{schema}.{table_name}
                    """
                )
                continue

            con.execute(
                f"""
                CREATE OR REPLACE VIEW {schema}.{table_name} AS
                {parquet_selects}
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
