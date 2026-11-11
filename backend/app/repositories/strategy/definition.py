from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.strategy import StrategyDefinition


class StrategyDefinitionRepository:
    """Read and idempotently upsert the built-in strategy definition catalog."""

    def ensure(
        self,
        db: Session,
        *,
        name: str,
        version: str,
        display_name: str,
        description: str,
        category: str,
        parameters_schema: dict[str, Any] | None = None,
        locked_parameters: list[str] | None = None,
        required_margin: Any = None,
        parameters: dict[str, Any] | None = None,
        inputs: list[str] | None = None,
        is_active: bool = True,
        status: str = "active",
    ) -> StrategyDefinition:
        """Create or refresh one strategy definition.

        The call is idempotent on ``(name, version)``: re-registering the same
        definition updates the existing row instead of inserting a second one,
        so the registry can run on every start-up.

        ``required_margin`` is written as an explicit column rather than
        buried in ``parameters_schema`` so that a locked margin can be
        queried, reported and asserted on without parsing JSON.  It is stored
        as a ``Decimal``; the schema JSON only describes its contract.

        ``parameters`` carries the *values* of the remaining locked
        thresholds.  ``required_margin`` is deliberately absent from it so
        that one number never lives in two places.
        """
        definition = self.get_by_name_version(db, name=name, version=version)
        values = {
            "display_name": display_name,
            "description": description,
            "category": category,
            "parameters_schema": parameters_schema or {},
            "locked_parameters": locked_parameters or [],
            "required_margin": required_margin,
            "parameters": parameters or {},
            "inputs": inputs or [],
            "is_active": is_active,
            "status": status,
        }
        if definition is None:
            definition = StrategyDefinition(name=name, version=version, **values)
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
    ) -> StrategyDefinition | None:
        return db.scalar(
            select(StrategyDefinition).where(
                StrategyDefinition.name == name,
                StrategyDefinition.version == version,
            )
        )

    def get_active(
        self,
        db: Session,
        *,
        name: str,
    ) -> StrategyDefinition | None:
        """Return the active definition of ``name`` (highest version wins)."""
        return db.scalar(
            select(StrategyDefinition)
            .where(
                StrategyDefinition.name == name,
                StrategyDefinition.status == "active",
                StrategyDefinition.is_active.is_(True),
            )
            .order_by(StrategyDefinition.version.desc())
        )

    def list(
        self,
        db: Session,
        *,
        name: str | None = None,
        category: str | None = None,
        is_active: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[StrategyDefinition], int]:
        filters = []
        if name:
            filters.append(StrategyDefinition.name == name)
        if category:
            filters.append(StrategyDefinition.category == category)
        if is_active is not None:
            filters.append(StrategyDefinition.is_active.is_(is_active))

        total = db.scalar(
            select(func.count()).select_from(StrategyDefinition).where(*filters)
        ) or 0
        items = list(
            db.scalars(
                select(StrategyDefinition)
                .where(*filters)
                .order_by(StrategyDefinition.name, StrategyDefinition.version)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total
