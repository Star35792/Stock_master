from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.research_session import initialize_research_database
from app.database.session import (
    apply_schema_compatibility,
    ensure_duckdb_sequences,
    initialize_database,
)
from app.models.strategy import BacktestRun, TrainingArtifact
from app.models.system import JobLog


def test_fresh_database_preserves_referenced_training_artifacts(tmp_path) -> None:
    engine = create_engine(f"duckdb:///{tmp_path / 'fresh.duckdb'}")
    initialize_database(engine)
    initialize_research_database(engine)
    initialize_database(engine)
    initialize_research_database(engine)
    with Session(engine) as db:
        artifact = TrainingArtifact(
            window_id="retained-window",
            strategy_name="test",
            factor_specs=[],
            training_start_date=date(2025, 1, 1),
            training_end_date=date(2025, 12, 31),
            test_start_date=date(2026, 1, 1),
            test_end_date=date(2026, 1, 31),
            training_input_snapshot={},
            artifact_hash="0" * 64,
            code_version="test",
            data_version="test",
        )
        db.add(artifact)
        db.flush()
        artifact_id = artifact.id
        run_values = dict(
            name="test",
            strategy_name="test",
            parameters={},
            code_version="test",
            data_version="test",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            initial_cash=Decimal("1000"),
        )
        linked_run = BacktestRun(**run_values, training_artifact_id=artifact_id)
        db.add_all([linked_run, BacktestRun(**run_values, training_artifact_id=None)])
        db.commit()
        run_id = linked_run.id
        with pytest.raises(IntegrityError):
            db.execute(delete(TrainingArtifact).where(TrainingArtifact.id == artifact_id))
            db.commit()
        db.rollback()
        assert db.get(BacktestRun, run_id).training_artifact_id == artifact_id
        assert db.get(TrainingArtifact, artifact_id) is not None
        assert db.scalar(select(BacktestRun.id).where(BacktestRun.training_artifact_id.is_(None)))
    engine.dispose()


def test_schema_compatibility_adds_release_lineage_columns(tmp_path) -> None:
    engine = create_engine(f"duckdb:///{tmp_path / 'legacy.duckdb'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA factor"))
        connection.execute(text("CREATE SCHEMA market"))
        connection.execute(text("CREATE TABLE factor.factor_run (id BIGINT)"))
        connection.execute(text("CREATE TABLE market.adjustment_factor (id BIGINT)"))

    apply_schema_compatibility(engine)
    apply_schema_compatibility(engine)

    with engine.connect() as connection:
        columns = set(
            connection.execute(
                text("""
                    SELECT table_schema, table_name, column_name
                    FROM information_schema.columns
                    WHERE (table_schema, table_name) IN (
                        ('factor', 'factor_run'),
                        ('market', 'adjustment_factor')
                    )
                """)
            ).all()
        )

    assert ("factor", "factor_run", "release_id") in columns
    assert ("market", "adjustment_factor", "release_id") in columns
    engine.dispose()


def test_legacy_factor_profiles_default_to_research_only_evidence(tmp_path) -> None:
    engine = create_engine(f"duckdb:///{tmp_path / 'legacy-profile.duckdb'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA factor"))
        connection.execute(text("CREATE TABLE factor.factor_profile (id BIGINT)"))
        connection.execute(text("INSERT INTO factor.factor_profile VALUES (7)"))

    apply_schema_compatibility(engine)
    apply_schema_compatibility(engine)

    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT evidence_eligibility, evidence_eligibility_reason "
                "FROM factor.factor_profile WHERE id = 7"
            )
        ).one()
    assert row == ("research_only", "unverified_evidence_eligibility")
    engine.dispose()


def test_schema_compatibility_removes_legacy_security_name_index(tmp_path) -> None:
    engine = create_engine(f"duckdb:///{tmp_path / 'legacy-security.duckdb'}")
    initialize_database(engine)
    initialize_research_database(engine)
    with engine.begin() as connection:
        connection.execute(text("CREATE INDEX ix_security_name ON market.security(name)"))

    apply_schema_compatibility(engine)
    apply_schema_compatibility(engine)

    with engine.connect() as connection:
        assert connection.scalar(text("""
            SELECT count(*) FROM duckdb_indexes()
            WHERE schema_name='market' AND table_name='security'
              AND index_name='ix_security_name'
        """)) == 0
    engine.dispose()


def test_existing_backtest_database_gets_nullable_artifact_column_and_index(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'old-backtest.duckdb'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA strategy"))
        connection.execute(text("""
            CREATE TABLE strategy.backtest_run (
                id BIGINT PRIMARY KEY, name VARCHAR, strategy_name VARCHAR,
                parameters JSON, code_version VARCHAR, data_version VARCHAR,
                start_date DATE, end_date DATE, initial_cash DECIMAL(24,4),
                final_equity DECIMAL(24,4), total_return DECIMAL(18,10),
                status VARCHAR, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
                error_message VARCHAR, created_at TIMESTAMPTZ
            )
        """))
        connection.execute(text("""
            INSERT INTO strategy.backtest_run
            (id, name, strategy_name, parameters, code_version, data_version,
             start_date, end_date, initial_cash, status)
            VALUES (7, 'existing', 'test', '{}', 'old', 'old',
                    '2026-01-01', '2026-01-31', 1000, 'success')
        """))
    initialize_database(engine)
    initialize_research_database(engine)
    initialize_database(engine)
    initialize_research_database(engine)
    with Session(engine) as db:
        run = db.get(BacktestRun, 7)
        assert run.name == "existing"
        assert run.initial_cash == Decimal("1000")
        assert run.training_artifact_id is None
        assert db.scalar(text("""
            SELECT count(*) FROM duckdb_indexes()
            WHERE schema_name='strategy' AND table_name='backtest_run'
              AND index_name='ix_backtest_run_training_artifact'
        """)) == 1
    engine.dispose()


def test_sequence_repair_restarts_above_retained_ids(tmp_path) -> None:
    engine = create_engine(f"duckdb:///{tmp_path / 'slimmed.duckdb'}")
    initialize_database(engine)
    with engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE metadata.job_log ALTER COLUMN id DROP DEFAULT")
        )
        connection.execute(text("DROP SEQUENCE system_job_log_id_seq"))
        connection.execute(
            text("""
                INSERT INTO metadata.job_log (id, job_name, job_type)
                VALUES (900, 'retained', 'schedule')
            """)
        )

    ensure_duckdb_sequences(engine)

    with Session(engine) as db:
        row = JobLog(job_name="after-slim", job_type="schedule")
        db.add(row)
        db.flush()
        assert row.id == 901
        db.rollback()
    engine.dispose()


def _sequence_default_alters(engine: Engine) -> list[str]:
    """Return the sequence-default DDL a connection would issue."""
    captured: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        if "SET DEFAULT" in statement.upper():
            captured.append(statement)

    try:
        initialize_database(engine)
    finally:
        event.remove(engine, "before_cursor_execute", _record)
    return captured


def test_fresh_database_needs_no_sequence_default_ddl(tmp_path) -> None:
    """A new database must be created with its defaults, not patched afterwards.

    ``CREATE TABLE`` carrying a default expression is safe to replay, while
    ``ALTER COLUMN ... SET DEFAULT nextval(...)`` is not (duckdb/duckdb#18259).
    Declaring the default on the model keeps the first initialization free of
    any statement that could strand the database.
    """
    engine = create_engine(f"duckdb:///{tmp_path / 'fresh.duckdb'}")
    alters = _sequence_default_alters(engine)
    with engine.connect() as connection:
        defaults = connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE column_default LIKE '%nextval%'"
            )
        )
    engine.dispose()

    assert alters == []
    assert defaults == 40


def test_repeated_initialization_does_not_reissue_sequence_default_ddl(tmp_path) -> None:
    """Re-initializing an existing database must not re-emit the DDL.

    The defaults are already correct after the first initialization, so the
    schema bridge has nothing to do.  Re-issuing the statement would write an
    unreplayable WAL entry on every single start.
    """
    database = tmp_path / "repeat.duckdb"
    engine = create_engine(f"duckdb:///{database}")
    initialize_database(engine)
    engine.dispose()

    engine = create_engine(f"duckdb:///{database}")
    alters = _sequence_default_alters(engine)
    engine.dispose()

    assert alters == []


def test_sequence_default_ddl_is_never_written_to_the_wal(tmp_path) -> None:
    """A crash after initialization must leave a database that still opens.

    DuckDB cannot replay a WAL entry that carries a default expression
    (duckdb/duckdb#18259), so a process killed before the next checkpoint used
    to leave a database that could not be opened again.  The schema upgrade is
    checkpointed, so the WAL only ever holds ordinary row changes.
    """
    database = tmp_path / "checkpointed.duckdb"

    def reopen() -> int:
        engine = create_engine(f"duckdb:///{database}")
        try:
            with engine.connect() as connection:
                return connection.scalar(
                    text("SELECT count(*) FROM metadata.job_log")
                )
        finally:
            engine.dispose()

    engine = create_engine(f"duckdb:///{database}")
    initialize_database(engine)
    # Write a row and keep the WAL: this is what a killed process leaves behind.
    with engine.begin() as connection:
        connection.execute(text("PRAGMA disable_checkpoint_on_shutdown"))
        connection.execute(
            text(
                "INSERT INTO metadata.job_log (job_name, job_type) "
                "VALUES ('probe', 'schedule')"
            )
        )
    engine.dispose()

    assert reopen() == 1
