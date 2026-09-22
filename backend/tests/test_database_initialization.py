from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.session import apply_schema_compatibility, initialize_database
from app.models.strategy import BacktestRun, TrainingArtifact


def test_fresh_database_preserves_referenced_training_artifacts(tmp_path) -> None:
    engine = create_engine(f"duckdb:///{tmp_path / 'fresh.duckdb'}")
    initialize_database(engine)
    initialize_database(engine)
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
    initialize_database(engine)
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
