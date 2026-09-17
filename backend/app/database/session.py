from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import settings


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
        "ALTER TABLE factor.factor_run ADD COLUMN IF NOT EXISTS release_id VARCHAR",
        "ALTER TABLE market.adjustment_factor "
        "ADD COLUMN IF NOT EXISTS release_id VARCHAR",
    )
    with bind.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def initialize_database(bind: Engine | None = None) -> None:
    """Create missing schemas/tables and upgrade supported legacy layouts."""
    resolved_bind = bind or engine
    _create_schemas(resolved_bind)

    # Import model modules lazily so Base.metadata is complete without creating
    # a session/models import cycle at module load time.
    import app.models  # noqa: F401
    from app.database.base import Base

    Base.metadata.create_all(bind=resolved_bind)
    apply_schema_compatibility(resolved_bind)


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
