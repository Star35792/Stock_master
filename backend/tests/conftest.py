"""Test-wide fixtures and dependency overrides.

The backend has migrated from PostgreSQL to DuckDB. API tests boot the full
FastAPI app, so we override ``get_db`` with an in-memory DuckDB instance that
has all schemas and tables pre-created.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import SCHEMAS, get_db
from app.main import app

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
