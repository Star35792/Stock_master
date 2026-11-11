from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.system import DataSource


class DataSourceRepository:
    """Registry of the external feeds the pipeline is allowed to trust.

    ``metadata.data_source`` is the single place that answers "which vendor
    produced this number, and how much do we trust it".  It stayed empty while
    ``raw.daily_bar`` already carried three distinct ``source_name`` values,
    so the provenance of every bar lived only in the raw rows and nothing could
    rank one feed above another.

    ``priority`` follows the same convention as ``raw.daily_bar.source_priority``:
    **lower wins**.  A candidate bar is taken from the lowest-priority source
    that has it, and every other source that also has it becomes a comparison
    peer rather than a competing truth.
    """

    def list(
        self,
        db: Session,
        *,
        is_active: bool | None = None,
        source_type: str | None = None,
    ) -> list[DataSource]:
        filters = []
        if is_active is not None:
            filters.append(DataSource.is_active.is_(is_active))
        if source_type:
            filters.append(DataSource.source_type == source_type)
        return list(
            db.scalars(
                select(DataSource)
                .where(*filters)
                .order_by(DataSource.priority, DataSource.source_name)
            )
        )

    def get_by_name(self, db: Session, source_name: str) -> DataSource | None:
        return db.scalar(
            select(DataSource).where(DataSource.source_name == source_name)
        )

    def upsert(
        self,
        db: Session,
        *,
        source_name: str,
        source_type: str,
        priority: int,
        is_active: bool = True,
        description: str | None = None,
        config: dict | None = None,
    ) -> DataSource:
        """Register a feed, or update its registration in place.

        Idempotent on ``source_name``: re-running a registration script never
        creates a duplicate row, and it never resets a field the caller did
        not mention (``config`` is replaced wholesale only when given).
        """
        row = self.get_by_name(db, source_name)
        if row is None:
            row = DataSource(source_name=source_name)
            db.add(row)

        row.source_type = source_type
        row.priority = priority
        row.is_active = is_active
        if description is not None:
            row.description = description
        if config is not None:
            row.config = config
        return row

    def get_priorities(self, db: Session) -> dict[str, int]:
        """Return ``{source_name: priority}`` for every registered feed.

        Used to rank raw candidates when a feed has not been registered yet:
        an unregistered source is treated as *lowest* trust rather than as
        silently taking precedence.
        """
        return {
            row.source_name: row.priority
            for row in db.scalars(select(DataSource))
        }
