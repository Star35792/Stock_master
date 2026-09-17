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
atexit.register(shutil.rmtree, _test_db_root, ignore_errors=True)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.database.base import Base  # noqa: E402
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
