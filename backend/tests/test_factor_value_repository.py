"""Tests for the batch factor-value upsert.

Why this file exists
--------------------
:meth:`FactorValueRepository.upsert_value` sends one ``INSERT ... ON CONFLICT``
per row.  A factor covering 20 sessions × 5,500 symbols is ~110k rows, so the
per-row path made the whole factor pipeline unusable (measured: 22 minutes
without finishing).  :meth:`FactorValueRepository.upsert_values` batches the
same work.

The batch path must therefore be **semantically identical** to the per-row
path — same conflict key, same "last write wins" behaviour, same return
count.  These tests pin that equivalence so a future optimisation cannot
quietly change what gets stored.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database.session import initialize_database
from app.models.factor import FactorValue
from app.repositories.factor.factor_value import FactorValueRepository

_TRADE_DATE = date(2026, 7, 1)


@pytest.fixture
def db_session(tmp_path) -> Session:
    engine = create_engine(f"duckdb:///{tmp_path / 'factor_value.duckdb'}")
    initialize_database(engine)
    session = Session(engine)
    yield session
    session.close()
    engine.dispose()


def _row(symbol: str, value: str, *, run_id: int = 1, name: str = "ret_1d") -> dict:
    return {
        "symbol": symbol,
        "trade_date": _TRADE_DATE,
        "factor_name": name,
        "factor_version": "v1",
        "value": Decimal(value),
        "run_id": run_id,
    }


def _stored(db: Session) -> dict[tuple[str, str, str, str], tuple[Decimal, int]]:
    return {
        (row.symbol, row.trade_date.isoformat(), row.factor_name, row.factor_version): (
            row.value,
            row.run_id,
        )
        for row in db.scalars(select(FactorValue))
    }


def test_upsert_values_writes_every_row_and_returns_count(db_session: Session) -> None:
    repository = FactorValueRepository()

    written = repository.upsert_values(
        db_session,
        [_row("600519.SH", "1.5"), _row("000001.SZ", "2.5")],
    )

    assert written == 2
    stored = _stored(db_session)
    assert stored[("600519.SH", "2026-07-01", "ret_1d", "v1")] == (Decimal("1.5"), 1)
    assert stored[("000001.SZ", "2026-07-01", "ret_1d", "v1")] == (Decimal("2.5"), 1)


def test_upsert_values_returns_zero_for_empty_input(db_session: Session) -> None:
    repository = FactorValueRepository()

    assert repository.upsert_values(db_session, []) == 0
    assert _stored(db_session) == {}


def test_upsert_values_keeps_last_row_on_duplicate_key(db_session: Session) -> None:
    """Same conflict key twice in one batch → the last value wins.

    This mirrors the per-row path, where the later ``ON CONFLICT DO UPDATE``
    simply overwrites the earlier one.
    """

    repository = FactorValueRepository()

    written = repository.upsert_values(
        db_session,
        [_row("600519.SH", "1.0"), _row("600519.SH", "9.0", run_id=7)],
    )

    assert written == 1
    stored = _stored(db_session)
    assert stored[("600519.SH", "2026-07-01", "ret_1d", "v1")] == (Decimal("9.0"), 7)


def test_upsert_values_overwrites_existing_rows(db_session: Session) -> None:
    repository = FactorValueRepository()

    repository.upsert_values(db_session, [_row("600519.SH", "1.0")])
    written = repository.upsert_values(db_session, [_row("600519.SH", "3.0", run_id=5)])

    assert written == 1
    stored = _stored(db_session)
    assert stored[("600519.SH", "2026-07-01", "ret_1d", "v1")] == (Decimal("3.0"), 5)


def test_upsert_values_splits_into_batches_without_losing_rows(
    db_session: Session,
) -> None:
    """A batch size smaller than the payload must still store every row."""

    repository = FactorValueRepository()
    rows = [_row(f"{index:06d}.SZ", str(index)) for index in range(25)]

    written = repository.upsert_values(db_session, rows, batch_size=7)

    assert written == 25
    assert len(_stored(db_session)) == 25


def test_batch_and_per_row_paths_agree(db_session: Session) -> None:
    """The two paths must produce byte-identical table state."""

    repository = FactorValueRepository()
    rows = [_row("600519.SH", "1.0"), _row("000001.SZ", "2.0"), _row("300750.SZ", "3.0")]

    repository.upsert_values(db_session, rows)
    batched = _stored(db_session)

    for row in rows:
        repository.upsert_value(
            db_session,
            symbol=row["symbol"],
            trade_date=row["trade_date"],
            factor_name=row["factor_name"],
            factor_version=row["factor_version"],
            value=row["value"],
            run_id=row["run_id"],
        )
    db_session.commit()

    assert _stored(db_session) == batched
