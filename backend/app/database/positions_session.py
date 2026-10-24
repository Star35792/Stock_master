"""SQLite engine/session for the real-position ledger.

The position book is the only domain stored outside the single DuckDB
database: it is a manually kept personal ledger, so a lightweight local
SQLite file keeps it separable from the market-data pipeline. Market
prices for valuation still come from the DuckDB ``market`` trusted layer.
"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import settings


@event.listens_for(Engine, "connect")
def _configure_sqlite_connection(dbapi_connection, connection_record) -> None:
    """Keep SQLite durable and FK-enforced; inert for every other dialect."""
    if type(dbapi_connection).__module__ != "sqlite3":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


Path(settings.positions_db_path).parent.mkdir(parents=True, exist_ok=True)
positions_engine = create_engine(
    settings.positions_database_url,
    connect_args={"check_same_thread": False},
)
PositionsSessionLocal = sessionmaker(
    bind=positions_engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def initialize_positions_database(bind: Engine | None = None) -> None:
    """Create missing ledger tables; SQLite has no schemas to pre-create."""
    resolved_bind = bind or positions_engine

    import app.models.positions  # noqa: F401
    from app.database.positions_base import PositionsBase

    PositionsBase.metadata.create_all(bind=resolved_bind)


def get_positions_db() -> Generator[Session]:
    db = PositionsSessionLocal()
    try:
        yield db
    finally:
        db.close()
