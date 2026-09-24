from datetime import date

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.data_sources import SecurityRecord
from app.database.session import initialize_database
from app.models.market import Security
from app.repositories.market.security import SecurityRepository


def _record(number: int, *, name: str | None = None, list_date: date | None = None):
    code = f"{number:06d}"
    return SecurityRecord(
        symbol=f"{code}.SZ",
        raw_symbol=code,
        name=name or f"Company {code}",
        security_type="stock",
        exchange="SZ",
        list_date=list_date,
    )


def test_identical_security_refresh_does_not_mutate_existing_index(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'securities.duckdb'}")
    initialize_database(engine)
    records = [_record(number) for number in range(1, 704)]
    repository = SecurityRepository()
    with Session(engine) as db:
        repository.upsert_many(db, records=records, source_name="hithink_finance")
        db.commit()

    mutations: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith(
            ("INSERT INTO MARKET.SECURITY", "UPDATE MARKET.SECURITY")
        ):
            mutations.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with Session(engine) as db:
            assert repository.upsert_many(db, records=records, source_name="hithink_finance") == 703
            db.commit()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert mutations == []
    with Session(engine) as db:
        db.add(Security(
            symbol="999999.SZ", name="ORM insert", security_type="stock",
            exchange="SZ", currency="CNY", is_active=True,
        ))
        db.commit()
        assert db.scalar(select(Security.id).where(Security.symbol == "999999.SZ")) > 703
    engine.dispose()


def test_security_refresh_fills_list_date_and_preserves_known_name(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'security-list-date.duckdb'}")
    initialize_database(engine)
    repository = SecurityRepository()
    with Session(engine) as db:
        repository.upsert_many(
            db, records=[_record(1, name="原有名称")], source_name="hithink_finance"
        )
        db.commit()
        repository.upsert_many(
            db,
            records=[_record(1, name="\ufffd\ufffd", list_date=date(2000, 1, 1))],
            source_name="hithink_finance",
        )
        db.commit()
        row = db.scalar(select(Security).where(Security.symbol == "000001.SZ"))
        assert row.name == "原有名称"
        assert row.list_date == date(2000, 1, 1)
    engine.dispose()


def test_conflicting_existing_listing_date_fails_without_overwrite(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'listing-conflict.duckdb'}")
    initialize_database(engine)
    repository = SecurityRepository()
    with Session(engine) as db:
        repository.upsert_many(
            db, records=[_record(1, list_date=date(2000, 1, 1))],
            source_name="hithink_finance",
        )
        db.commit()
        with pytest.raises(ValueError, match="conflicting existing listing date"):
            repository.upsert_many(
                db, records=[_record(1, list_date=date(2001, 1, 1))],
                source_name="hithink_finance",
            )
        db.rollback()
        assert db.scalar(select(Security.list_date)) == date(2000, 1, 1)
    engine.dispose()
