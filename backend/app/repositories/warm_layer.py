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
        # Research-layer valuation history.  It lives in the ``research``
        # namespace on purpose: the rows are collected today for historical
        # dates, so they are *not* part of the trusted ``market`` consumption
        # layer and must not be picked up by generic valuation readers.
        ("research", "valuation_history"),
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
    ("research", "valuation_history"): "trade_date",
}

# Business keys used when a delta release overlays an ancestor.  The newest
# release wins for the same key, while genuinely new historical rows from the
# parent remain visible.
WARM_TABLE_KEYS: dict[tuple[str, str], tuple[str, ...]] = {
    ("market", "daily_bar"): ("symbol", "trade_date", "adjust_type"),
    ("market", "adjustment_factor"): (
        "symbol",
        "trade_date",
        "adjust_type",
        "source_name",
        "version",
    ),
    ("raw", "daily_bar"): ("id",),
    ("factor", "factor_value"): (
        "symbol",
        "trade_date",
        "factor_name",
        "factor_version",
    ),
    ("raw", "corporate_action"): ("id",),
    ("fundamental", "financial_statement"): (
        "symbol",
        "statement_type",
        "period",
        "fiscal_year",
        "fiscal_period",
    ),
    ("raw", "financial_payload"): ("id",),
    ("research", "valuation_history"): ("symbol", "trade_date"),
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

# Upper bound for the ``base_release_tag`` chain walk.  Releases are a DAG in
# practice; the bound only protects against a malformed cycle.
_MAX_RELEASE_DEPTH = 16


def _aligned_select(columns: list[str], glob_columns: list[str]) -> str:
    """Build a SELECT list aligned to ``columns`` for one parquet glob.

    Columns the glob does not carry are emitted as ``NULL AS <column>`` so
    that the ``UNION ALL`` of several release exports stays positionally
    aligned even when the exports disagree on their column set.
    """
    parts = []
    for column in columns:
        if column in glob_columns:
            parts.append(column)
        else:
            parts.append(f"NULL AS {column}")
    return ", ".join(parts)


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
        """Return the most recent *successful* release tag directory name.

        An interrupted or legacy failed export may leave a directory behind.
        Selecting it would make research read a partial release. Only releases
        whose summary reports ``all_ok=true`` are eligible.
        """
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
        successful = [tag for tag in candidates if self._release_is_complete(tag)]
        if not successful:
            return None
        # Tags are YYYYMMDD-HHMMSS; lexicographic sort == chronological sort.
        return sorted(successful)[-1]

    def _release_is_complete(self, release_tag: str) -> bool:
        """Return whether a release finished its export successfully.

        A missing or unreadable summary is treated as incomplete: an
        unverified release must never become the fact source.
        """
        summary = self.release_path(release_tag) / "export_summary.json"
        if not summary.exists():
            return False
        try:
            payload = json.loads(summary.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        return payload.get("all_ok") is True

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

    def table_mode_for(
        self, release_tag: str, schema: str, table: str
    ) -> str | None:
        """Return a table's release mode (``snapshot`` or ``delta``), if declared."""
        summary = self.release_path(release_tag) / "export_summary.json"
        if not summary.exists():
            return None
        try:
            payload = json.loads(summary.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        for item in payload.get("tables", []) if isinstance(payload, dict) else []:
            if (
                isinstance(item, dict)
                and item.get("schema") == schema
                and item.get("table") == table
            ):
                mode = item.get("mode")
                return mode if mode in {"snapshot", "delta"} else None
        return None

    def release_lineage(self, release_tag: str | None = None) -> list[str]:
        """Return the release chain starting at ``release_tag``.

        The chain follows ``base_release_tag`` links upwards: the requested
        release first, then its parent, then the grandparent, and so on.  A
        single-level fallback is not enough once releases are stacked (a
        factor backfill on top of a data release on top of the full release),
        because the middle release may not carry the table a reader needs.
        Invalid cycles and chains beyond the depth limit fail closed.
        """
        tag = release_tag or self.latest_release_tag
        if tag is None:
            return []
        chain: list[str] = []
        seen: set[str] = set()
        current: str | None = tag
        while current and current not in seen and len(chain) < _MAX_RELEASE_DEPTH:
            seen.add(current)
            chain.append(current)
            current = self.base_release_tag_for(current)
        if current in seen:
            raise ValueError(f"warm release lineage contains a cycle: {tag}")
        if current:
            raise ValueError(
                f"warm release lineage exceeds {_MAX_RELEASE_DEPTH} levels: {tag}"
            )
        return chain

    def table_globs(
        self,
        schema: str,
        table: str,
        release_tag: str | None = None,
    ) -> list[Path]:
        """Return the glob paths for a warm table's parquet partitions.

        Tables absent from the requested release fall back to the parent
        release, so a release can carry only the tables it changed.  The whole
        ancestor chain is consulted, which keeps a table readable even when
        releases are stacked several levels deep.

        Only directories that actually exist are returned.  A table that is
        missing from the entire chain yields an empty list, and the caller
        skips it instead of pointing DuckDB at a non-existent path.
        """
        tag = release_tag or self.latest_release_tag
        if tag is None:
            raise ValueError(
                f"no warm-layer release found under {self._exported_root()}"
            )
        globs: list[Path] = []
        seen: set[Path] = set()
        # The lineage is ordered newest-first, and that order matters: the
        # release chain is not guaranteed to export the same column set (an
        # older release may still carry the surrogate ``id`` column that a
        # newer one dropped), so the *newest* release defines the view's
        # shape.  An explicitly configured base is therefore only appended
        # when the lineage does not already contain it; prepending it would
        # silently make an ancestor define the schema.
        candidates: list[str] = list(self.release_lineage(tag))
        if self.base_release_tag and self.base_release_tag not in candidates:
            candidates.append(self.base_release_tag)
        for candidate in candidates:
            if not candidate:
                continue
            path = self._exported_root() / candidate / schema / table
            if path.exists() and path not in seen:
                seen.add(path)
                globs.append(path)
                # A full snapshot supersedes the same table in every ancestor.
                # Delta releases intentionally continue walking the lineage.
                if self.table_mode_for(candidate, schema, table) == "snapshot":
                    break
        return globs

    def prices_qfq_glob(self) -> Path:
        """Return the glob path for the curated forward-adjusted price layer.

        During a publish, the old directory is briefly named ``.replacing-*``.
        A crash in that window must not make the sole materialization unreadable.
        """
        lake_root = Path(settings.data_lake_root).resolve()
        curated = lake_root / "curated"
        primary = curated / "prices_qfq"
        if primary.exists():
            return primary
        replacing = sorted(
            (p for p in curated.glob(".prices_qfq.replacing-*") if p.is_dir()),
            key=lambda path: path.stat().st_mtime,
        )
        if replacing:
            return replacing[-1]
        # Legacy publishers left timestamped directories. Keep the last one
        # readable if a crash removed the primary before the new publish.
        backups = sorted(p for p in curated.glob("prices_qfq.backup-*") if p.is_dir())
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
            # Warm tables that do not exist in the control plane are still
            # exposed: a Parquet-only release (e.g. a research dataset) has no
            # control-plane counterpart, so enumerating the control catalog
            # alone would hide it from every attached caller.
            for schema, table_name in sorted(WARM_TABLES):
                if (schema, table_name) not in table_rows:
                    table_rows.append((schema, table_name))
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
            if not globs:
                # This table is absent from the whole release chain.  Skip it
                # instead of pointing DuckDB at a non-existent path: a new
                # warm table must not break the releases that do not carry it.
                continue
            parquet_globs = [f"{g.as_posix()}/**/*.parquet" for g in globs]
            # Parquet files include hive partition columns (trade_year,
            # trade_month) that the control-plane table does not have.  Build an
            # explicit column list so UNION ALL stays aligned.
            #
            # Releases in one chain do not necessarily export the same column
            # set: ``factor.factor_value`` was exported with a surrogate
            # ``id`` column before that column was dropped from the export.
            # Reusing the first glob's columns for every glob would make the
            # UNION ALL reference a column a later glob does not have, and
            # DuckDB raises ``Binder Error: Referenced column "id" not
            # found``.  Align every glob to the union of the column sets and
            # fill the columns it lacks with NULL.
            partition_cols = {"trade_year", "trade_month"}
            columns_by_glob: list[list[str]] = []
            for pattern in parquet_globs:
                col_rows = con.execute(
                    f"""
                    DESCRIBE
                    SELECT *
                    FROM read_parquet('{pattern}', hive_partitioning=true)
                    """
                ).fetchall()
                columns_by_glob.append(
                    [c[0] for c in col_rows if c[0] not in partition_cols]
                )
            columns: list[str] = []
            for glob_columns in columns_by_glob:
                for column in glob_columns:
                    if column not in columns:
                        columns.append(column)
            if not columns:
                raise ValueError(
                    f"no columns found for warm table {schema}.{table_name}"
                )
            col_sql = ", ".join(columns)

            date_col = WARM_TABLE_DATE_COLUMNS[(schema, table_name)]
            parquet_union = " UNION ALL ".join(
                f"SELECT {_aligned_select(columns, glob_columns)}, "
                f"{rank} AS __release_rank "
                f"FROM read_parquet('{pattern}', hive_partitioning=true)"
                for rank, (pattern, glob_columns) in enumerate(
                    zip(parquet_globs, columns_by_glob, strict=True)
                )
            )
            key_columns = WARM_TABLE_KEYS[(schema, table_name)]
            missing_keys = [key for key in key_columns if key not in columns]
            if missing_keys:
                raise ValueError(
                    f"warm table {schema}.{table_name} is missing key columns: "
                    f"{missing_keys}"
                )
            key_sql = ", ".join(key_columns)
            parquet_selects = f"""
                SELECT {col_sql}
                FROM ({parquet_union})
                QUALIFY row_number() OVER (
                    PARTITION BY {key_sql}
                    ORDER BY __release_rank
                ) = 1
            """
            if attach_control:
                # A warm table may exist only in Parquet (a research dataset
                # has no control-plane counterpart).  Detect that case instead
                # of unioning against a table that does not exist.
                control_has_table = con.execute(
                    """
                    SELECT count(*)
                    FROM information_schema.tables
                    WHERE table_catalog = 'ctrl'
                      AND table_schema = ?
                      AND table_name = ?
                      AND table_type = 'BASE TABLE'
                    """,
                    [schema, table_name],
                ).fetchone()[0]
                if not control_has_table:
                    con.execute(
                        f"""
                        CREATE OR REPLACE VIEW {schema}.{table_name} AS
                        {parquet_selects}
                        """
                    )
                    continue
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
