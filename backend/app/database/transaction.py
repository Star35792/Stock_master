"""Transaction helpers tuned for DuckDB's concurrency model."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

# DuckDB uses optimistic concurrency (no row locks). When two transactions
# touch the same row, the loser aborts with a "Conflict on update"/"Conflict on
# delete" TransactionException, surfaced by duckdb-engine as OperationalError.
_CONFLICT_MARKER = "conflict"


def is_write_conflict(exc: OperationalError) -> bool:
    """Return True if ``exc`` is a DuckDB optimistic-concurrency write conflict."""
    return _CONFLICT_MARKER in str(exc).lower()


def retry_on_conflict[T](
    db: Session,
    operation: Callable[[], T],
    *,
    attempts: int = 5,
) -> T:
    """Run ``operation`` under DuckDB, retrying on optimistic write conflicts.

    Postgres serialized concurrent writers with ``SELECT ... FOR UPDATE`` row
    locks. DuckDB instead aborts the losing writer with a conflict error, so we
    roll the session back and retry. The retried attempt re-reads current state
    (including any idempotency row the winner just committed) and either replays
    that result or proceeds against the updated rows.
    """
    last_exc: OperationalError | None = None
    for _ in range(attempts):
        try:
            return operation()
        except OperationalError as exc:
            if not is_write_conflict(exc):
                raise
            db.rollback()
            last_exc = exc
    assert last_exc is not None
    raise last_exc


@contextmanager
def idempotent_write(db: Session) -> Iterator[None]:
    """Group writes whose uniqueness conflict should trigger an idempotent replay.

    This replaces ``Session.begin_nested()`` (SAVEPOINT), which DuckDB does not
    support. DuckDB aborts the entire transaction on a constraint violation, so
    we flush inside the block to surface the ``IntegrityError`` eagerly, then roll
    the session back before re-raising. Callers catch the ``IntegrityError``,
    look up the already-committed row by idempotency key, and replay it against a
    clean session.
    """
    try:
        yield
        db.flush()
    except IntegrityError:
        db.rollback()
        raise
