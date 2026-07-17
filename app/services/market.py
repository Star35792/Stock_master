from datetime import date

from sqlalchemy.orm import Session

from app.repositories.market import (
    AdjustmentFactorRepository,
    CorporateActionRepository,
    DailyBarRepository,
    DailyBarSourceCompareRepository,
    ManualCorrectionRepository,
    SecurityRepository,
)
from app.schemas import (
    AdjustmentFactorRead,
    CorporateActionRead,
    DailyBarRead,
    DailyBarSourceCompareRead,
    ManualCorrectionRead,
    MissingDailyBarRead,
    Page,
    SecurityRead,
)


class MarketQueryService:
    def __init__(
        self,
        security_repository: SecurityRepository | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
        source_compare_repository: DailyBarSourceCompareRepository | None = None,
        manual_correction_repository: ManualCorrectionRepository | None = None,
        corporate_action_repository: CorporateActionRepository | None = None,
        adjustment_factor_repository: AdjustmentFactorRepository | None = None,
    ) -> None:
        self.security_repository = security_repository or SecurityRepository()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.source_compare_repository = (
            source_compare_repository or DailyBarSourceCompareRepository()
        )
        self.manual_correction_repository = (
            manual_correction_repository or ManualCorrectionRepository()
        )
        self.corporate_action_repository = (
            corporate_action_repository or CorporateActionRepository()
        )
        self.adjustment_factor_repository = (
            adjustment_factor_repository or AdjustmentFactorRepository()
        )

    def list_securities(
        self,
        db: Session,
        *,
        security_type: str | None,
        exchange: str | None,
        is_active: bool | None,
        keyword: str | None,
        limit: int,
        offset: int,
    ) -> Page[SecurityRead]:
        rows, total = self.security_repository.list(
            db,
            security_type=security_type,
            exchange=exchange,
            is_active=is_active,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[SecurityRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_daily_bars(
        self,
        db: Session,
        *,
        symbol: str,
        adjust_type: str,
        start_date: date | None,
        end_date: date | None,
        quality_status: str | None,
        limit: int,
        offset: int,
    ) -> Page[DailyBarRead]:
        rows, total = self.daily_bar_repository.list_by_symbol(
            db,
            symbol=symbol.upper(),
            adjust_type=adjust_type,
            start_date=start_date,
            end_date=end_date,
            quality_status=quality_status,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[DailyBarRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_missing_daily_bars(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbol: str | None,
        security_type: str | None,
        exchange: str | None,
        limit: int,
        offset: int,
    ) -> Page[MissingDailyBarRead]:
        rows, total = self.daily_bar_repository.list_missing_by_calendar(
            db,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            symbol=symbol.upper() if symbol else None,
            security_type=security_type,
            exchange=exchange,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[
                MissingDailyBarRead.model_validate(
                    row._mapping if hasattr(row, "_mapping") else row
                )
                for row in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_source_compares(
        self,
        db: Session,
        *,
        symbol: str | None,
        start_date: date | None,
        end_date: date | None,
        adjust_type: str | None,
        is_conflict: bool | None,
        limit: int,
        offset: int,
    ) -> Page[DailyBarSourceCompareRead]:
        rows, total = self.source_compare_repository.list(
            db,
            symbol=symbol.upper() if symbol else None,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            is_conflict=is_conflict,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[DailyBarSourceCompareRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_corporate_actions(
        self,
        db: Session,
        *,
        symbol: str | None,
        start_date: date | None,
        end_date: date | None,
        action_type: str | None,
        source_name: str | None,
        limit: int,
        offset: int,
    ) -> Page[CorporateActionRead]:
        rows, total = self.corporate_action_repository.list(
            db,
            symbol=symbol.upper() if symbol else None,
            start_date=start_date,
            end_date=end_date,
            action_type=action_type,
            source_name=source_name,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[CorporateActionRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_adjustment_factors(
        self,
        db: Session,
        *,
        symbol: str | None,
        start_date: date | None,
        end_date: date | None,
        adjust_type: str | None,
        source_name: str | None,
        version: str | None,
        quality_status: str | None,
        limit: int,
        offset: int,
    ) -> Page[AdjustmentFactorRead]:
        rows, total = self.adjustment_factor_repository.list(
            db,
            symbol=symbol.upper() if symbol else None,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            source_name=source_name,
            version=version,
            quality_status=quality_status,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[AdjustmentFactorRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_manual_corrections(
        self,
        db: Session,
        *,
        symbol: str | None,
        start_date: date | None,
        end_date: date | None,
        adjust_type: str | None,
        field_name: str | None,
        corrected_by: str | None,
        limit: int,
        offset: int,
    ) -> Page[ManualCorrectionRead]:
        rows, total = self.manual_correction_repository.list(
            db,
            symbol=symbol.upper() if symbol else None,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            field_name=field_name,
            corrected_by=corrected_by,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[ManualCorrectionRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
