"""Engine/session for the standalone research-artifact database.

The strategy backtest tables (``backtest_run`` & co., ``training_artifact``)
move to ``data/research.duckdb`` so research scripts and the API/maintenance
processes no longer contend for one file-level DuckDB write lock:

* Research scripts are the primary writer here; they take
  ``duckdb_write_lock`` (research lock file) around their sessions.
* The API opens this database per request (NullPool) and takes the same lock
  with a short timeout, answering 409 "research busy" while a long backtest
  write is in flight.
* Market/factor/calendar reads still come from the main database or the warm
  Parquet layer; only artifacts are stored here.
"""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config.settings import settings

RESEARCH_SCHEMAS = ("strategy",)


def _create_schemas(bind: Engine) -> None:
    with bind.connect() as conn:
        for schema in RESEARCH_SCHEMAS:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()


Path(settings.research_db_path).parent.mkdir(parents=True, exist_ok=True)
research_engine = create_engine(
    f"duckdb:///{settings.research_db_path}",
    poolclass=NullPool,
)
ResearchSessionLocal = sessionmaker(
    bind=research_engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def initialize_research_database(bind: Engine | None = None) -> None:
    """Create missing research tables in the standalone database."""
    from app.utils.duckdb_write_lock import duckdb_write_lock

    resolved_bind = bind or research_engine

    import app.models  # noqa: F401  (registers every model)
    from app.database.research_base import ResearchBase

    # Startup must coordinate with research scripts just like request sessions.
    # Explicit engines are isolated test/maintenance targets and use their own lock.
    lock_path = (
        Path(str(resolved_bind.url.database)).with_suffix(".initialize.lock")
        if bind is not None
        else Path("data/research_write.lock")
    )
    with duckdb_write_lock(lock_path, timeout_seconds=3.0, poll_interval_seconds=0.25):
        _create_schemas(resolved_bind)
        ResearchBase.metadata.create_all(bind=resolved_bind)


def get_research_db() -> Generator[Session]:
    # DuckDB excludes even read-only opens while another process holds the
    # file read-write, so during a research run API queries would fail with
    # an opaque IO error. Take the same research write lock with a short
    # timeout and surface a clean 409 instead; the lock file doubles as the
    # "who is holding it" indicator.
    from fastapi import HTTPException, status

    from app.utils.duckdb_write_lock import DuckDbWriteLockTimeout, duckdb_write_lock

    try:
        with duckdb_write_lock(
            "data/research_write.lock",
            timeout_seconds=3.0,
            poll_interval_seconds=0.25,
        ):
            db = ResearchSessionLocal()
            try:
                yield db
            finally:
                db.close()
    except DuckDbWriteLockTimeout as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"research database busy: {exc}",
        ) from exc
