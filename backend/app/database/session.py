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
    "metadata",
)

Path(settings.metadata_db_path).parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(settings.database_url)
with engine.connect() as conn:
    for schema in SCHEMAS:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    conn.commit()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
