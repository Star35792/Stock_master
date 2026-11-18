from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Sequence, create_engine, delete, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.research_session import initialize_research_database
from app.database.session import (
    apply_schema_compatibility,
    ensure_duckdb_sequences,
    initialize_database,
)
from app.models.portfolio import PortfolioPlanRecord, PortfolioTargetRecord
from app.models.strategy import BacktestRun, TrainingArtifact
from app.models.system import JobLog
from app.portfolio import PortfolioPlan, PortfolioTarget
from app.repositories.portfolio import PortfolioPlanRepository
from app.services.portfolio import PortfolioConstructionService


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


def test_legacy_portfolio_plan_columns_upgrade_with_existing_rows(tmp_path) -> None:
    engine = create_engine(f"duckdb:///{tmp_path / 'legacy-portfolio.duckdb'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA portfolio"))
        conn.execute(text("CREATE SEQUENCE portfolio_portfolio_plan_id_seq START 2"))
        conn.execute(text("CREATE SEQUENCE portfolio_portfolio_target_id_seq START 2"))
        conn.execute(text("""
            CREATE TABLE portfolio.portfolio_plan (
                id BIGINT PRIMARY KEY DEFAULT nextval('portfolio_portfolio_plan_id_seq'),
                name VARCHAR(100) NOT NULL, as_of_date DATE NOT NULL,
                parameters JSON NOT NULL, total_equity DECIMAL(24,4) NOT NULL,
                cash_before DECIMAL(24,4) NOT NULL, target_cash DECIMAL(24,4) NOT NULL,
                cash_after DECIMAL(24,4) NOT NULL, code_version VARCHAR(100) NOT NULL,
                idempotency_key VARCHAR(100), request_hash VARCHAR(64),
                input_snapshot JSON, created_at TIMESTAMPTZ DEFAULT current_timestamp
            )
        """))
        conn.execute(text("""
            CREATE TABLE portfolio.portfolio_target (
                id BIGINT PRIMARY KEY DEFAULT nextval('portfolio_portfolio_target_id_seq'),
                plan_id BIGINT NOT NULL, symbol VARCHAR(20) NOT NULL,
                security_type VARCHAR(20) NOT NULL, industry VARCHAR(100) NOT NULL,
                requested_weight DECIMAL(18,10) NOT NULL,
                constrained_weight DECIMAL(18,10) NOT NULL,
                final_weight DECIMAL(18,10) NOT NULL,
                current_quantity INTEGER NOT NULL, target_quantity INTEGER NOT NULL,
                order_quantity INTEGER NOT NULL, price DECIMAL(18,6) NOT NULL,
                reasons JSON NOT NULL, created_at TIMESTAMPTZ DEFAULT current_timestamp
            )
        """))
        conn.execute(text(
            "CREATE UNIQUE INDEX uq_portfolio_plan_idempotency_key "
            "ON portfolio.portfolio_plan (idempotency_key)"
        ))
        conn.execute(text(
            "CREATE INDEX ix_portfolio_target_plan ON portfolio.portfolio_target (plan_id)"
        ))
        conn.execute(text("""
            INSERT INTO portfolio.portfolio_plan
            (id, name, as_of_date, parameters, total_equity, cash_before,
             target_cash, cash_after, code_version)
            VALUES (1, 'legacy', '2026-01-05', '{}', 1000, 1000, 500, 500, 'legacy')
        """))
        conn.execute(text("""
            INSERT INTO portfolio.portfolio_target
            (id, plan_id, symbol, security_type, industry, requested_weight,
             constrained_weight, final_weight, current_quantity, target_quantity,
             order_quantity, price, reasons)
            VALUES (1, 1, '000001.SZ', 'stock', 'bank', 0.5, 0.5, 0.5,
                    0, 100, 100, 5, '[]')
        """))

    apply_schema_compatibility(engine)
    apply_schema_compatibility(engine)

    with engine.connect() as conn:
        columns = dict(
            conn.execute(
                text(
                    "SELECT table_name || '.' || column_name, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_schema = 'portfolio' AND "
                    "((table_name = 'portfolio_plan' AND column_name = 'style_risk') OR "
                    "(table_name = 'portfolio_target' AND column_name = 'score'))"
                )
            ).all()
        )
    assert columns == {
        "portfolio_plan.style_risk": "JSON",
        "portfolio_target.score": "DECIMAL(18,10)",
    }
    with Session(engine) as db:
        old_plan = db.get(PortfolioPlanRecord, 1)
        old_target = db.scalar(
            select(PortfolioTargetRecord).where(PortfolioTargetRecord.plan_id == 1)
        )
        assert old_plan.style_risk is None
        assert old_target.score is None
        assert PortfolioConstructionService().get_plan(db, plan_id=1).style_risk is None
        old_target.score = Decimal("0.1234567890")
        saved_plan = PortfolioPlanRepository().save(
            db,
            name="new",
            as_of_date=date(2026, 1, 6),
            parameters={},
            plan=PortfolioPlan(
                total_equity=Decimal("2000"),
                cash_before=Decimal("2000"),
                target_cash=Decimal("1000"),
                cash_after=Decimal("1000"),
                targets=(
                    PortfolioTarget(
                        symbol="600000.SH",
                        security_type="stock",
                        industry="bank",
                        requested_weight=Decimal("0.5"),
                        constrained_weight=Decimal("0.5"),
                        final_weight=Decimal("0.5"),
                        current_quantity=0,
                        target_quantity=200,
                        order_quantity=200,
                        price=Decimal("5"),
                        score=Decimal("0.9"),
                        reasons=("lot_rounding",),
                    ),
                ),
            ),
            code_version="current",
            idempotency_key="legacy-upgrade-new",
            request_hash="a" * 64,
            input_snapshot={},
        )
        assert saved_plan.id == 2
        db.commit()
    with Session(engine) as db:
        assert db.scalar(
            select(PortfolioTargetRecord.score).where(PortfolioTargetRecord.plan_id == 1)
        ) == Decimal("0.1234567890")
        plan, targets = PortfolioPlanRepository().get_with_targets(db, plan_id=2)
        assert plan.name == "new"
        assert targets[0].score == Decimal("0.9")
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
    # Every sequence-defaulted column of the model layer must have its
    # default baked into ``CREATE TABLE``.  The expected count is derived
    # from the metadata instead of hard-coded, so adding a table does not
    # turn this into a brittle number, while a *missing* default still
    # fails the test.
    expected_defaults = sum(
        1
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if isinstance(column.default, Sequence)
    )
    assert defaults == expected_defaults


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
