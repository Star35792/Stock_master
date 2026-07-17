from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.factor import FactorDefinition, FactorRun


class FactorRunRepository:
    def create(
        self,
        db: Session,
        *,
        definition: FactorDefinition,
        parameters: dict[str, Any],
        code_version: str,
        input_start_date: date | None,
        input_end_date: date | None,
        metadata: dict[str, Any] | None = None,
    ) -> FactorRun:
        run = FactorRun(
            definition_id=definition.id,
            factor_name=definition.name,
            factor_version=definition.version,
            parameters=parameters,
            code_version=code_version,
            input_start_date=input_start_date,
            input_end_date=input_end_date,
            status="running",
            metadata_=metadata,
        )
        db.add(run)
        return run

    def list(
        self,
        db: Session,
        *,
        factor_name: str | None = None,
        factor_version: str | None = None,
        status: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[FactorRun], int]:
        filters = []
        if factor_name:
            filters.append(FactorRun.factor_name == factor_name)
        if factor_version:
            filters.append(FactorRun.factor_version == factor_version)
        if status:
            filters.append(FactorRun.status == status)
        if start_date:
            filters.append(
                FactorRun.started_at
                >= datetime.combine(start_date, datetime.min.time(), UTC)
            )
        if end_date:
            filters.append(
                FactorRun.started_at <= datetime.combine(end_date, datetime.max.time(), UTC)
            )

        total = db.scalar(select(func.count()).select_from(FactorRun).where(*filters)) or 0
        items = list(
            db.scalars(
                select(FactorRun)
                .where(*filters)
                .order_by(FactorRun.started_at.desc(), FactorRun.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def update_status(
        self,
        db: Session,
        *,
        run_id: int,
        status: str,
        rows_written: int = 0,
        error_message: str | None = None,
    ) -> FactorRun | None:
        run = db.get(FactorRun, run_id)
        if run is None:
            return None

        run.status = status
        run.rows_written = rows_written
        run.error_message = error_message
        run.finished_at = datetime.now(UTC)
        db.add(run)
        return run
