from datetime import date

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database.session import initialize_database
from app.repositories.fundamental import (
    FinancialStatementRevisionRepository,
    statement_content_hash,
)
from app.repositories.market import SecurityIndustryHistoryRepository


def _statement_row(
    *,
    net_profit: str = "100",
    report_date: date = date(2026, 4, 30),
) -> dict:
    return {
        "symbol": "600519.SH",
        "statement_type": "income",
        "period": "annual",
        "fiscal_year": 2025,
        "fiscal_period": "FY",
        "report_date": report_date,
        "period_end": date(2025, 12, 31),
        "currency": "CNY",
        "metrics": {"net_profit": net_profit},
        "source_name": "hithink_finance",
    }


@pytest.fixture()
def db(tmp_path) -> Session:
    engine = create_engine(f"duckdb:///{tmp_path / 'revisions.duckdb'}")
    initialize_database(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def test_statement_content_hash_is_deterministic_and_content_scoped() -> None:
    row = _statement_row()
    assert statement_content_hash(row) == statement_content_hash(dict(row))
    # A metric change is a new revision.
    changed = _statement_row(net_profit="120")
    assert statement_content_hash(changed) != statement_content_hash(row)
    # A changed announcement date is a new revision.
    moved = _statement_row(report_date=date(2026, 5, 6))
    assert statement_content_hash(moved) != statement_content_hash(row)


def test_revision_repository_appends_and_reads_history(db: Session) -> None:
    repository = FinancialStatementRevisionRepository()

    first = repository.append(
        db,
        row=_statement_row(),
        revision_no=repository.next_revision_no(
            db,
            symbol="600519.SH",
            statement_type="income",
            period="annual",
            fiscal_year=2025,
            fiscal_period="FY",
        ),
        row_hash=statement_content_hash(_statement_row()),
        batch_id=1,
    )
    db.commit()
    assert first.revision_no == 1

    restated = _statement_row(net_profit="120")
    second = repository.append(
        db,
        row=restated,
        revision_no=repository.next_revision_no(
            db,
            symbol="600519.SH",
            statement_type="income",
            period="annual",
            fiscal_year=2025,
            fiscal_period="FY",
        ),
        row_hash=statement_content_hash(restated),
        batch_id=2,
    )
    db.commit()
    assert second.revision_no == 2

    history = repository.list_for_statement(
        db,
        symbol="600519.SH",
        statement_type="income",
        period="annual",
        fiscal_year=2025,
        fiscal_period="FY",
    )
    assert [row.revision_no for row in history] == [1, 2]
    assert history[0].metrics["net_profit"] == "100"
    assert history[1].metrics["net_profit"] == "120"
    assert history[1].batch_id == 2

    latest = repository.latest_by_key(db, symbols=["600519.SH"])
    assert len(latest) == 1
    key = ("600519.SH", "income", "annual", 2025, "FY")
    assert latest[key].revision_no == 2
    assert latest[key].row_hash == statement_content_hash(restated)

    # current_statement reads the live (latest-content) statement table.
    assert (
        repository.current_statement(
            db,
            symbol="600519.SH",
            statement_type="income",
            period="annual",
            fiscal_year=2025,
            fiscal_period="FY",
        )
        is None
    )


def test_revision_table_exists_after_initialization(db: Session) -> None:
    count = db.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='fundamental' "
            "AND table_name='financial_statement_revision'"
        )
    )
    assert count == 1


def test_industry_history_resolves_as_of_windows(db: Session) -> None:
    repository = SecurityIndustryHistoryRepository()

    repository.record_interval(
        db,
        symbol="600519.SH",
        industry="食品饮料",
        effective_from=date(2020, 1, 1),
        effective_to=None,
        source_name="hithink",
    )
    repository.record_interval(
        db,
        symbol="600519.SH",
        industry="银行",
        effective_from=date(2024, 7, 1),
        effective_to=None,
        source_name="hithink_restated",
    )
    db.commit()

    # The open interval was closed at the new interval's start.
    rows = db.scalars(
        text(
            "SELECT industry FROM market.security_industry_history "
            "WHERE symbol='600519.SH' ORDER BY effective_from"
        )
    ).all()
    assert rows == ["食品饮料", "银行"]

    resolved = repository.industries_as_of(
        db, symbols={"600519.SH", "000001.SZ"}, as_of=date(2024, 6, 30)
    )
    assert resolved == {"600519.SH": "食品饮料"}

    resolved = repository.industries_as_of(db, symbols={"600519.SH"}, as_of=date(2024, 7, 1))
    assert resolved == {"600519.SH": "银行"}

    resolved = repository.industries_as_of(db, symbols={"600519.SH"}, as_of=date(2030, 1, 1))
    assert resolved == {"600519.SH": "银行"}

    assert repository.industry_as_of(db, symbol="600519.SH", as_of=date(2019, 1, 1)) is None
    assert repository.industry_as_of(db, symbol="000001.SZ", as_of=date(2024, 7, 1)) is None


def test_industry_history_rejects_backwards_interval(db: Session) -> None:
    repository = SecurityIndustryHistoryRepository()
    repository.record_interval(
        db,
        symbol="600519.SH",
        industry="食品饮料",
        effective_from=date(2024, 1, 1),
        effective_to=None,
        source_name="hithink",
    )
    db.commit()

    with pytest.raises(ValueError, match="must start after the open interval"):
        repository.record_interval(
            db,
            symbol="600519.SH",
            industry="银行",
            effective_from=date(2023, 1, 1),
            effective_to=None,
            source_name="hithink_restated",
        )


def test_security_industry_history_table_exists_after_initialization(
    db: Session,
) -> None:
    count = db.scalar(
        text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='market' "
            "AND table_name='security_industry_history'"
        )
    )
    assert count == 1
    assert db.scalar(text("SELECT count(*) FROM market.security_industry_history")) == 0
    assert db.scalar(text("SELECT count(*) FROM fundamental.financial_statement_revision")) == 0


def test_collection_writes_revision_history_and_stays_idempotent(
    db: Session,
) -> None:
    """A restated report keeps the superseded content as an earlier revision."""

    from app.models.fundamental import FinancialStatement, FinancialStatementRevision
    from app.services.hithink_financial_collection import _upsert_statement

    item = {
        "thscode": "600519.SH",
        "period": "annual",
        "fiscal_year": 2025,
        "fiscal_period": "FY",
        "report_date_ms": 1777612800000,
        "period_end_ms": 1767225600000,
        "currency": "CNY",
        "net_profit": 100,
    }

    _upsert_statement(db, "hithink_finance", "income", item, batch_id=1)
    db.commit()
    # Identical content must not create a second revision.
    _upsert_statement(db, "hithink_finance", "income", dict(item), batch_id=2)
    db.commit()
    # A restated report creates revision 2 and updates the current row.
    _upsert_statement(db, "hithink_finance", "income", dict(item, net_profit=120), batch_id=3)
    db.commit()

    statement = db.query(FinancialStatement).one()
    assert statement.metrics["net_profit"] == 120
    revisions = (
        db.query(FinancialStatementRevision).order_by(FinancialStatementRevision.revision_no).all()
    )
    assert [(row.revision_no, row.metrics["net_profit"]) for row in revisions] == [
        (1, 100),
        (2, 120),
    ]
    assert [row.batch_id for row in revisions] == [1, 3]
    assert all(len(row.row_hash) == 64 for row in revisions)
