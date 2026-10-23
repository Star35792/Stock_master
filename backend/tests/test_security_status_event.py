from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.session import initialize_database
from app.repositories.market import SecurityStatusEventRepository


@pytest.fixture
def repo():
    return SecurityStatusEventRepository()


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'security-status.duckdb'}")
    initialize_database(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_record_status_and_read_back(db: Session, repo: SecurityStatusEventRepository) -> None:
    event = repo.record_status(
        db,
        symbol="600000.SH",
        trade_date=date(2026, 1, 5),
        status="suspended",
        source_name="test",
        source_type="manual",
        note="temporary halt",
    )
    db.commit()

    assert event.id is not None
    assert event.status == "suspended"

    status = repo.status_on_date(db, symbol="600000.SH", trade_date=date(2026, 1, 5))
    assert status == "suspended"


def test_statuses_on_date_returns_latest_observation(
    db: Session, repo: SecurityStatusEventRepository
) -> None:
    repo.record_status(
        db,
        symbol="600000.SH",
        trade_date=date(2026, 1, 5),
        status="suspended",
        source_name="test",
    )
    repo.record_status(
        db,
        symbol="600000.SH",
        trade_date=date(2026, 1, 7),
        status="normal",
        source_name="test",
    )
    db.commit()

    assert repo.status_on_date(db, symbol="600000.SH", trade_date=date(2026, 1, 6)) == "suspended"
    assert repo.status_on_date(db, symbol="600000.SH", trade_date=date(2026, 1, 7)) == "normal"
    assert repo.status_on_date(db, symbol="600000.SH", trade_date=date(2026, 1, 8)) == "normal"


def test_suspended_symbols_on_date(db: Session, repo: SecurityStatusEventRepository) -> None:
    repo.record_status(
        db,
        symbol="600000.SH",
        trade_date=date(2026, 1, 5),
        status="suspended",
        source_name="test",
    )
    repo.record_status(
        db,
        symbol="000001.SZ",
        trade_date=date(2026, 1, 5),
        status="normal",
        source_name="test",
    )
    db.commit()

    suspended = repo.suspended_symbols_on_date(
        db, symbols=["600000.SH", "000001.SZ"], trade_date=date(2026, 1, 5)
    )
    assert suspended == {"600000.SH"}


def test_invalid_status_rejected(db: Session, repo: SecurityStatusEventRepository) -> None:
    with pytest.raises(ValueError, match="invalid status"):
        repo.record_status(
            db,
            symbol="600000.SH",
            trade_date=date(2026, 1, 5),
            status="unknown",
            source_name="test",
        )


def test_unique_constraint_ignores_duplicate_observation(
    db: Session, repo: SecurityStatusEventRepository
) -> None:
    repo.record_status(
        db,
        symbol="600000.SH",
        trade_date=date(2026, 1, 5),
        status="suspended",
        source_name="test",
    )
    db.commit()

    # Same key should violate the unique constraint and raise IntegrityError.
    with pytest.raises(IntegrityError):
        repo.record_status(
            db,
            symbol="600000.SH",
            trade_date=date(2026, 1, 5),
            status="suspended",
            source_name="test",
        )
        db.commit()
