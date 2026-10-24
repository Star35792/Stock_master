"""Test-wide fixtures and dependency overrides.

The backend has migrated from PostgreSQL to DuckDB. API tests boot the full
FastAPI app, so we override ``get_db`` with an in-memory DuckDB instance that
has all schemas and tables pre-created.
"""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

# app.database.session creates its engine at import time. Force that module-level
# engine onto an isolated file before importing any app module, so collecting the
# test suite never opens data/stock_master.duckdb.
_test_db_root = Path(tempfile.mkdtemp(prefix="stock-master-tests-"))
os.environ["METADATA_DB_PATH"] = str(_test_db_root / "session.duckdb")
# The real-position ledger engine is created at import time too; point it at the
# same temp root so tests never touch data/positions.db.
os.environ["POSITIONS_DB_PATH"] = str(_test_db_root / "positions.db")
# Research artifacts live in their own DuckDB file in production; tests point
# it beside the temp root but the dependency override below maps it onto the
# same in-memory database so single-session test fixtures keep working.
os.environ["RESEARCH_DB_PATH"] = str(_test_db_root / "research.duckdb")
# The default research read path is the Parquet warm layer. Unit tests exercise
# the control-plane ORM semantics (and cannot self-ATTACH the temp DuckDB file),
# so they pin the control source here instead of touching every service call
# site. Tests that cover the warm path set ``research_read_source`` explicitly.
os.environ["RESEARCH_READ_SOURCE"] = "control"
# API tests must never launch vendor downloads using a developer's .env key.
os.environ["AUTO_DATA_SYNC_ENABLED"] = "false"
atexit.register(shutil.rmtree, _test_db_root, ignore_errors=True)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.database.base import Base  # noqa: E402
from app.database.positions_base import PositionsBase  # noqa: E402
from app.database.positions_session import get_positions_db  # noqa: E402
from app.database.session import SCHEMAS, get_db  # noqa: E402
from app.main import app  # noqa: E402

_test_engine = create_engine("duckdb:///:memory:")
with _test_engine.connect() as conn:
    for schema in SCHEMAS:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    conn.commit()
Base.metadata.create_all(_test_engine)
_TestSessionLocal = sessionmaker(bind=_test_engine, expire_on_commit=False)


def _override_get_db():
    db = _TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db

# Strategy artifact tables ride the same in-memory database in tests: both
# metadata objects create disjoint table sets, and service-level
# ``research_db=None`` falls back to the caller's session anyway.
from app.database.research_base import ResearchBase  # noqa: E402
from app.database.research_session import get_research_db  # noqa: E402

ResearchBase.metadata.create_all(_test_engine)
app.dependency_overrides[get_research_db] = _override_get_db

# Shared in-memory SQLite for the real-position ledger; StaticPool keeps every
# session on the same connection so the memory database actually persists.
_positions_engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
PositionsBase.metadata.create_all(_positions_engine)
_PositionsSessionLocal = sessionmaker(bind=_positions_engine, expire_on_commit=False)


def _override_get_positions_db():
    db = _PositionsSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_positions_db] = _override_get_positions_db
