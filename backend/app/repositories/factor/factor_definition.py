from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.factor import FactorDefinition


class FactorDefinitionRepository:
    def ensure(
        self,
        db: Session,
        *,
        name: str,
        display_name: str,
        description: str,
        category: str,
        frequency: str,
        inputs: list[str],
        applicable_security_types: list[str],
        window_days: int | None,
        version: str,
        dependencies: list[str] | None = None,
        parameters_schema: dict[str, Any] | None = None,
        is_active: bool = True,
        pit_status: str = "unknown",
        status: str = "active",
    ) -> FactorDefinition:
        """Create or refresh a factor definition.

        ``is_active``, ``pit_status`` and ``status`` are explicit arguments
        because all three change the factor's role in the pipeline:

        * a definition that is **not** active is skipped by the batch
          profiler, so registering a factor whose evidence is produced by a
          dedicated script does not let the batch run overwrite that evidence
          with empty cross-sections;
        * ``pit_status`` is what the pool classifier reads to decide whether
          the factor may enter the core pool.  ``unknown`` is the fail-closed
          default;
        * ``status='retired'`` marks a superseded or known-wrong definition.
          Its evidence is kept for audit but the factor is frozen.
        """
        definition = self.get_by_name_version(db, name=name, version=version)
        values = {
            "display_name": display_name,
            "description": description,
            "category": category,
            "frequency": frequency,
            "inputs": inputs,
            "applicable_security_types": applicable_security_types,
            "window_days": window_days,
            "dependencies": dependencies or [],
            "parameters_schema": parameters_schema or {},
            "is_active": is_active,
            "pit_status": pit_status,
            "status": status,
        }
        if definition is None:
            definition = FactorDefinition(name=name, version=version, **values)
        else:
            for field_name, value in values.items():
                setattr(definition, field_name, value)

        db.add(definition)
        db.flush()
        return definition

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
        category: str | None = None,
        frequency: str | None = None,
        is_active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[FactorDefinition], int]:
        filters = []
        if name:
            filters.append(FactorDefinition.name == name)
        if category:
            filters.append(FactorDefinition.category == category)
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
