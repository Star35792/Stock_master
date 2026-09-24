"""Allocate DuckDB sequence IDs after legacy explicit-ID bulk loads."""

from sqlalchemy import text
from sqlalchemy.orm import Session

_SEQUENCES = {"market_security_id_seq", "market_trading_calendar_id_seq"}


def reserve_sequence_ids(
    db: Session, *, sequence_name: str, max_existing_id: int, count: int
) -> list[int]:
    if sequence_name not in _SEQUENCES or count < 0:
        raise ValueError("invalid market sequence reservation")
    last_value = db.scalar(
        text("SELECT last_value FROM duckdb_sequences() WHERE sequence_name = :name"),
        {"name": sequence_name},
    ) or 0
    statement = text(f"SELECT nextval('{sequence_name}') FROM range(:count)")
    if last_value < max_existing_id:
        db.execute(statement, {"count": max_existing_id - last_value}).all()
    if count == 0:
        return []
    return [int(row[0]) for row in db.execute(statement, {"count": count}).all()]
