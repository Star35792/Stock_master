from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.factor import (
    FactorDefinitionRepository,
    FactorProfileRepository,
    FactorRunRepository,
    FactorValueRepository,
)
from app.schemas import (
    FactorDefinitionRead,
    FactorProfileRead,
    FactorRunRead,
    FactorValueRead,
    Page,
)


class FactorQueryService:
    def __init__(
        self,
        definition_repository: FactorDefinitionRepository | None = None,
        profile_repository: FactorProfileRepository | None = None,
        run_repository: FactorRunRepository | None = None,
        value_repository: FactorValueRepository | None = None,
    ) -> None:
        self.definition_repository = definition_repository or FactorDefinitionRepository()
        self.profile_repository = profile_repository or FactorProfileRepository()
        self.run_repository = run_repository or FactorRunRepository()
        self.value_repository = value_repository or FactorValueRepository()

    def list_definitions(
        self,
        db: Session,
        *,
        name: str | None,
        category: str | None,
        frequency: str | None,
        is_active: bool | None,
        limit: int,
        offset: int,
    ) -> Page[FactorDefinitionRead]:
        rows, total = self.definition_repository.list(
            db,
            name=name,
            category=category,
            frequency=frequency,
            is_active=is_active,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[FactorDefinitionRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_runs(
        self,
        db: Session,
        *,
        factor_name: str | None,
        factor_version: str | None,
        status: str | None,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
    ) -> Page[FactorRunRead]:
        rows, total = self.run_repository.list(
            db,
            factor_name=factor_name,
            factor_version=factor_version,
            status=status,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[FactorRunRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_values(
        self,
        db: Session,
        *,
        factor_name: str | None,
        factor_version: str | None,
        symbol: str | None,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
    ) -> Page[FactorValueRead]:
        rows, total = self.value_repository.list(
            db,
            factor_name=factor_name,
            factor_version=factor_version,
            symbol=symbol.upper() if symbol else None,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[FactorValueRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_profiles(
        self,
        db: Session,
        *,
        factor_name: str | None,
        factor_version: str | None,
        start_date: date | None,
        end_date: date | None,
        limit: int,
        offset: int,
    ) -> Page[FactorProfileRead]:
        rows, total = self.profile_repository.list(
            db,
            factor_name=factor_name,
            factor_version=factor_version,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[FactorProfileRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def start_run(
        self,
        db: Session,
        *,
        factor_name: str,
        factor_version: str,
        parameters: dict[str, Any],
        code_version: str,
        input_start_date: date | None,
        input_end_date: date | None,
        release_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> FactorRunRead:
        definition = self.definition_repository.get_by_name_version(
            db,
            name=factor_name,
            version=factor_version,
        )
        if definition is None:
            raise ValueError("factor definition not found")

        run = self.run_repository.create(
            db,
            definition=definition,
            parameters=parameters,
            code_version=code_version,
            release_id=release_id,
            input_start_date=input_start_date,
            input_end_date=input_end_date,
            metadata=metadata,
        )
        return FactorRunRead.model_validate(run)

    def record_value(
        self,
        db: Session,
        *,
        symbol: str,
        trade_date: date,
        factor_name: str,
        factor_version: str,
        value: Decimal,
        run_id: int,
    ) -> None:
        self.value_repository.upsert_value(
            db,
            symbol=symbol.upper(),
            trade_date=trade_date,
            factor_name=factor_name,
            factor_version=factor_version,
            value=value,
            run_id=run_id,
        )

    def finish_run(
        self,
        db: Session,
        *,
        run_id: int,
        rows_written: int,
        status: str = "success",
        error_message: str | None = None,
    ) -> FactorRunRead | None:
        run = self.run_repository.update_status(
            db,
            run_id=run_id,
            status=status,
            rows_written=rows_written,
            error_message=error_message,
        )
        if run is None:
            return None
        return FactorRunRead.model_validate(run)
