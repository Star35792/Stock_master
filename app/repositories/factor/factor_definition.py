from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.factor import FactorDefinition


class FactorDefinitionRepository:
    def get_by_name_version(
        self,
        db: Session,
        *,
        name: str,
        version: str,
    ) -> FactorDefinition | None:
        return db.scalar(
            select(FactorDefinition).where(
                FactorDefinition.name == name,
                FactorDefinition.version == version,
            )
        )

    def list(
        self,
        db: Session,
        *,
        name: str | None = None,
        frequency: str | None = None,
        is_active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[FactorDefinition], int]:
        filters = []
        if name:
            filters.append(FactorDefinition.name == name)
        if frequency:
            filters.append(FactorDefinition.frequency == frequency)
        if is_active is not None:
            filters.append(FactorDefinition.is_active.is_(is_active))

        total = db.scalar(select(func.count()).select_from(FactorDefinition).where(*filters)) or 0
        items = list(
            db.scalars(
                select(FactorDefinition)
                .where(*filters)
                .order_by(FactorDefinition.name, FactorDefinition.version)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total
