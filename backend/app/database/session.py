import logging
import re
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Sequence, create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import settings

logger = logging.getLogger(__name__)


@event.listens_for(Engine, "connect")
def _set_duckdb_session_timezone(dbapi_connection, connection_record) -> None:
    """Pin every DuckDB connection to UTC.

    DuckDB stores TIMESTAMPTZ as UTC but renders it in the session timezone on
    read. Without this, ``datetime.isoformat()`` round-trips shift the offset
    (e.g. ``+00:00`` -> ``+08:00``), which changes payload hashes even though the
    instant is unchanged. Pinning UTC keeps the process timezone-stable, matching
    the previous PostgreSQL-in-UTC behavior. Guarded to DuckDB connections so it
    stays inert for any other dialect.
    """
    if not type(dbapi_connection).__module__.startswith("duckdb"):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET TimeZone='UTC'")
    finally:
        cursor.close()


SCHEMAS = (
    "raw",
    "market",
    "fundamental",
    "factor",
    "strategy",
    "portfolio",
    "risk",
    "trade",
    "lake",
    "metadata",
)


def _create_schemas(bind: Engine) -> None:
    with bind.connect() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()


def apply_schema_compatibility(bind: Engine) -> None:
    """Apply idempotent upgrades required by pre-release DuckDB databases.

    ``MetaData.create_all`` creates new tables but does not add columns to
    existing tables. Keep the small compatibility bridge explicit until a
    versioned DuckDB migration runner replaces it.
    """
    statements = (
        ("factor.factor_run", "ALTER TABLE factor.factor_run "
         "ADD COLUMN IF NOT EXISTS release_id VARCHAR"),
        ("market.adjustment_factor", "ALTER TABLE market.adjustment_factor "
         "ADD COLUMN IF NOT EXISTS release_id VARCHAR"),
        # Special-treatment status is derived from the security's short name
        # and stored on the security master, so the daily bars and the screen
        # read one consistent value.  Additive and idempotent.
        ("market.security", "ALTER TABLE market.security "
         "ADD COLUMN IF NOT EXISTS special_treatment_status VARCHAR(20)"),
        # Strategy definitions carry their locked thresholds in a JSON column
        # so a run is reproducible from the catalog alone.  Nullable and
        # additive: a pre-existing row simply has an empty parameter set.
        ("strategy.strategy_definition", "ALTER TABLE strategy.strategy_definition "
         "ADD COLUMN IF NOT EXISTS parameters JSON"),
    )
    with bind.begin() as conn:
        for table, statement in statements:
            # An ALTER on a missing table is a hard error in DuckDB, and the
            # compatibility bridge runs before ``create_all`` has produced
            # every table in a fresh database.  The bridge is additive, so
            # skipping a table that does not exist yet is safe: the column
            # comes with the table when it is created.
            schema, name = table.split(".")
            exists = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_name = :name"
                ),
                {"schema": schema, "name": name},
            ).scalar_one()
            if not exists:
                continue
            conn.execute(text(statement))
        # DuckDB can invalidate the database when updating a security name
        # while this legacy ART index is present. Name searches use contains
        # matching over a small reference table, so remove the index.
        conn.execute(text("DROP INDEX IF EXISTS market.ix_security_name"))
    # DuckDB requires separate commits for these additive ALTERs on populated
    # tables. Every step is idempotent, so an interrupted upgrade can resume.
    for table in ("raw.daily_bar", "market.daily_bar"):
        with bind.connect() as conn:
            schema, name = table.split(".")
            existing_columns = set(
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :schema AND table_name = :name"
                    ),
                    {"schema": schema, "name": name},
                ).scalars()
            )
        if not existing_columns:
            continue
        for column in (
            "limit_up DECIMAL(18, 4)",
            "limit_down DECIMAL(18, 4)",
            "is_suspended BOOLEAN DEFAULT false",
            "special_treatment_status VARCHAR(20)",
        ):
            if column.split()[0] in existing_columns:
                continue
            with bind.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}"))

    # The factor evidence tables gained columns after the first release:
    #
    # * ``factor.factor_profile`` carries the multi-horizon endpoint
    #   (long-short) spreads used by the pool classifier's endpoint gate;
    # * ``factor.factor_definition`` carries the point-in-time status of the
    #   factor definition, which the classifier uses to fail closed.
    #
    # Both tables are populated, and DuckDB needs one commit per ALTER on a
    # populated table, so each column gets its own transaction.
    additive_factor_columns = (
        (
            "factor.factor_profile",
            (
                "long_short_forward_return_5d DECIMAL(24, 10)",
                "long_short_forward_return_20d DECIMAL(24, 10)",
                "evidence_eligibility VARCHAR(32) DEFAULT 'research_only'",
                "evidence_eligibility_reason VARCHAR(500) "
                "DEFAULT 'unverified_evidence_eligibility'",
            ),
        ),
        (
            "factor.factor_definition",
            (
                "pit_status VARCHAR(20) DEFAULT 'unknown'",
                "status VARCHAR(20) DEFAULT 'active'",
            ),
        ),
    )
    for table, columns in additive_factor_columns:
        schema, name = table.split(".")
        with bind.connect() as conn:
            existing_columns = set(
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :schema AND table_name = :name"
                    ),
                    {"schema": schema, "name": name},
                ).scalars()
            )
        if not existing_columns:
            continue
        for column in columns:
            if column.split()[0] in existing_columns:
                continue
            with bind.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}"))
        # ``ADD COLUMN ... DEFAULT`` only fills rows that exist at the time of
        # the ALTER.  Rows written before the column existed keep NULL, and
        # NULL is not a valid point-in-time verdict -- the fail-closed default
        # is ``unknown``.  Backfilling is idempotent.
        if table == "factor.factor_definition" and "pit_status" in existing_columns:
            with bind.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE factor.factor_definition "
                        "SET pit_status = 'unknown' WHERE pit_status IS NULL"
                    )
                )
        if table == "factor.factor_definition" and "status" in existing_columns:
            with bind.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE factor.factor_definition "
                        "SET status = 'active' WHERE status IS NULL"
                    )
                )
        if table == "factor.factor_profile" and "evidence_eligibility" in existing_columns:
            with bind.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE factor.factor_profile SET evidence_eligibility = 'research_only' "
                        "WHERE evidence_eligibility IS NULL"
                    )
                )

        if table == "factor.factor_profile" and "evidence_eligibility_reason" in existing_columns:
            with bind.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE factor.factor_profile "
                        "SET evidence_eligibility_reason = 'unverified_evidence_eligibility' "
                        "WHERE evidence_eligibility_reason IS NULL"
                    )
                )

    # Older plan tables predate persisted style exposure and target scores.
    # Both fields are optional; preserve existing rows as NULL. DuckDB needs
    # an individual commit for each ALTER on a populated table.
    for table, column in (
        ("portfolio.portfolio_plan", "style_risk JSON"),
        ("portfolio.portfolio_target", "score DECIMAL(18, 10)"),
    ):
        schema, name = table.split(".")
        with bind.connect() as conn:
            exists = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_catalog = current_database() "
                    "AND table_schema = :schema AND table_name = :name "
                    "AND column_name = :column"
                ),
                {"schema": schema, "name": name, "column": column.split()[0]},
            ).scalar_one()
            table_exists = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_catalog = current_database() "
                    "AND table_schema = :schema AND table_name = :name"
                ),
                {"schema": schema, "name": name},
            ).scalar_one()
        if table_exists and not exists:
            with bind.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column}"))

    # Existing pre-artifact databases need an additive upgrade: create_all does
    # not add columns or indexes to tables that are already present. Keep the
    # nullable column and its index in separate commits for DuckDB compatibility.
    with bind.connect() as conn:
        has_backtest_run = bool(conn.scalar(text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'strategy' AND table_name = 'backtest_run'"
        )))
    if has_backtest_run:
        with bind.begin() as conn:
            conn.execute(text(
                "ALTER TABLE strategy.backtest_run "
                "ADD COLUMN IF NOT EXISTS training_artifact_id BIGINT"
            ))
        with bind.begin() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_backtest_run_training_artifact "
                "ON strategy.backtest_run (training_artifact_id)"
            ))

    # TrainingArtifact gained a content hash so that backtest runs can bind to an
    # exact immutable training snapshot. Add the column idempotently for legacy
    # databases; new databases receive it from create_all.
    with bind.connect() as conn:
        has_training_artifact = bool(conn.scalar(text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'strategy' AND table_name = 'training_artifact'"
        )))
    if has_training_artifact:
        with bind.begin() as conn:
            conn.execute(text(
                "ALTER TABLE strategy.training_artifact "
                "ADD COLUMN IF NOT EXISTS artifact_hash VARCHAR(64)"
            ))
        with bind.begin() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_training_artifact_hash "
                "ON strategy.training_artifact (artifact_hash)"
            ))


def _same_sequence_default(current: str, expected: str) -> bool:
    """Compare two ``nextval(...)`` default expressions.

    DuckDB stores the default as the original expression text, so the two
    sides can differ in whitespace, letter case, or a schema qualifier on the
    sequence name.  Normalizing those differences keeps the comparison from
    degrading into "re-issue the DDL on every start".
    """

    def normalize(value: str) -> str:
        collapsed = "".join(value.split()).lower()
        # ``nextval('schema.seq')`` and ``nextval('seq')`` name the same
        # default when the sequence lives in the current schema.
        collapsed = re.sub(r"nextval\('[^.']*\.([^']+)'\)", r"nextval('\1')", collapsed)
        return collapsed

    return normalize(current) == normalize(expected)


def ensure_duckdb_sequences(bind: Engine) -> None:
    """Restore model id sequences/defaults after a CTAS-based control-plane slim.

    DuckDB ``CREATE TABLE AS SELECT`` does not preserve primary-key sequences or
    column defaults.  Every sequence is restarted strictly above the retained
    table maximum, so a rebuilt control plane cannot reuse historical ids.

    The default is only rewritten when it is actually missing or points at a
    different sequence.  Re-issuing ``ALTER COLUMN ... SET DEFAULT nextval(...)``
    on every start is not harmless: the default expression is logged to the WAL,
    and DuckDB cannot replay a WAL entry that carries a default expression
    (duckdb/duckdb#18259 -- "Failure while replaying WAL file ... Calling
    DatabaseManager::GetDefaultDatabase with no default database set").  A
    process that is killed between that DDL and the next checkpoint then leaves
    a database that no longer opens at all.  Skipping the redundant DDL keeps
    the WAL free of unreplayable entries.
    """
    if bind.dialect.name != "duckdb":
        return

    from app.database.base import Base

    bindings: list[tuple[str, str, str, str]] = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            sequence = column.default if isinstance(column.default, Sequence) else None
            if sequence is not None:
                bindings.append(
                    (table.schema or "main", table.name, column.name, sequence.name)
                )

    with bind.begin() as conn:
        # ``information_schema`` and ``duckdb_sequences()`` span every attached
        # catalog, so the queries below must be restricted to the catalog this
        # engine owns.  Without that filter an attached database with a
        # same-named table turns the lookup into multiple rows.
        existing = set(
            conn.execute(
                text(
                    "SELECT sequence_name FROM duckdb_sequences() "
                    "WHERE database_name = current_database()"
                )
            )
            .scalars()
            .all()
        )
        for schema, table, column, sequence_name in bindings:
            table_exists = conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_catalog = current_database() "
                    "AND table_schema=:schema AND table_name=:table"
                ),
                {"schema": schema, "table": table},
            ).scalar_one()
            if not table_exists:
                continue
            if sequence_name not in existing:
                maximum = conn.execute(
                    text(f'SELECT coalesce(max("{column}"), 0) FROM "{schema}"."{table}"')
                ).scalar_one()
                start = int(maximum or 0) + 1
                conn.execute(
                    text(f'CREATE SEQUENCE "{sequence_name}" START {start}')
                )
                existing.add(sequence_name)
            expected = f"nextval('{sequence_name}')"
            current = conn.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_catalog = current_database() "
                    "AND table_schema=:schema AND table_name=:table AND column_name=:column"
                ),
                {"schema": schema, "table": table, "column": column},
            ).scalar_one_or_none()
            if current is not None and _same_sequence_default(current, expected):
                continue
            if current is not None:
                # A mismatch means the stored default points somewhere else.
                # Rewrite it, but say so: a silent mismatch would degrade back
                # to re-issuing the DDL on every start.
                logger.warning(
                    "sequence default mismatch on %s.%s.%s: expected %s, found %s",
                    schema,
                    table,
                    column,
                    expected,
                    current,
                )
            conn.execute(
                text(
                    f'ALTER TABLE "{schema}"."{table}" '
                    f'ALTER COLUMN "{column}" SET DEFAULT {expected}'
                )
            )


def initialize_database(bind: Engine | None = None) -> None:
    """Create missing schemas/tables and upgrade supported legacy layouts."""
    resolved_bind = bind or engine
    _create_schemas(resolved_bind)

    # Import model modules lazily so Base.metadata is complete without creating
    # a session/models import cycle at module load time.
    import app.models  # noqa: F401
    from app.database.base import Base

    Base.metadata.create_all(bind=resolved_bind)
    ensure_duckdb_sequences(resolved_bind)
    apply_schema_compatibility(resolved_bind)
    checkpoint_duckdb(resolved_bind)


def checkpoint_duckdb(bind: Engine) -> None:
    """Fold the WAL into the database file so it stays replayable.

    DuckDB cannot replay a WAL entry that carries a default expression
    (duckdb/duckdb#18259).  Schema upgrades legitimately produce such entries,
    so a process killed before the next checkpoint would leave a database that
    cannot be opened at all.  Checkpointing right after the upgrade moves the
    DDL into the database file and truncates the WAL, which keeps a later
    crash recoverable.

    A failed checkpoint must not stop the process from starting: the database
    itself stays healthy, and the WAL simply keeps the DDL it already had.  The
    failure is logged so an operator can rerun the checkpoint, rather than
    taking the whole service down over a fold that already succeeded
    elsewhere.
    """
    if bind.dialect.name != "duckdb":
        return
    try:
        with bind.connect() as conn:
            conn.execute(text("CHECKPOINT"))
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - a checkpoint failure is reportable
        logger.warning("schema upgrade checkpoint failed: %s", exc)


Path(settings.metadata_db_path).parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(settings.database_url)
_create_schemas(engine)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
