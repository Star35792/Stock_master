from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.factors.registry import register_all_factor_definitions
from app.schemas import (
    FactorBatchProfileRunRequest,
    FactorBatchProfileRunResponse,
    FactorDefinitionRead,
    FactorPoolClassificationRead,
    FactorPoolClassificationRequest,
    FactorProfileItem,
    FactorProfileRead,
    FactorProfileSummaryRead,
    FactorRunRead,
    FactorValueRead,
    Page,
)
from app.services import (
    FactorBatchProfilerService,
    FactorPoolClassification,
    FactorPoolClassifierService,
    FactorPoolPolicy,
    FactorProfileSummary,
    FactorQueryService,
)

router = APIRouter(prefix="/factors", tags=["factors"])
service = FactorQueryService()
batch_profiler_service = FactorBatchProfilerService()
pool_classifier_service = FactorPoolClassifierService()


def _summary_to_read(
    summary: FactorProfileSummary,
) -> FactorProfileSummaryRead:
    return FactorProfileSummaryRead(
        **vars(summary),
        has_ic_evidence=summary.has_ic_evidence,
    )


@router.get("/definitions", response_model=Page[FactorDefinitionRead])
def list_factor_definitions(
    db: DatabaseSession,
    name: str | None = None,
    category: str | None = None,
    frequency: Literal["daily", "weekly", "monthly"] | None = None,
    is_active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FactorDefinitionRead]:
    return service.list_definitions(
        db,
        name=name,
        category=category,
        frequency=frequency,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )


@router.post("/definitions/register-all")
def register_all_definitions(db: DatabaseSession) -> dict[str, object]:
    """Register all factor specs as definition catalog entries (idempotent)."""
    counts = register_all_factor_definitions(db)
    db.commit()
    return {"registered": counts}


@router.post("/batch-profiles", response_model=FactorBatchProfileRunResponse)
def run_batch_factor_profiles(
    payload: FactorBatchProfileRunRequest,
    db: DatabaseSession,
) -> FactorBatchProfileRunResponse:
    """Batch-run cross-section diagnostics for all active factor definitions.

    Each factor runs in its own transaction; a single failure does not abort
    the batch, and the whole run is recorded in ``system.job_log``.
    """
    if payload.start_date > payload.end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )
    try:
        result = batch_profiler_service.run(
            db,
            start_date=payload.start_date,
            end_date=payload.end_date,
            adjust_type=payload.adjust_type,
            categories=set(payload.categories) if payload.categories else None,
            excluded_categories=(
                set(payload.excluded_categories) if payload.excluded_categories else None
            ),
            size_factor_name=payload.size_factor_name,
            size_factor_version=payload.size_factor_version,
            code_version=payload.code_version,
            release_id=payload.release_id,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return FactorBatchProfileRunResponse(
        start_date=result.start_date,
        end_date=result.end_date,
        adjust_type=result.adjust_type,
        success_count=result.success_count,
        failed_count=result.failed_count,
        total_profile_rows=result.total_profile_rows,
        total_transformed_rows=result.total_transformed_rows,
        results=[FactorProfileItem(**vars(item)) for item in result.results],
    )


@router.get("/batch-profiles/summary", response_model=Page[FactorProfileSummaryRead])
def list_factor_profile_summaries(
    db: DatabaseSession,
    factor_name: str | None = None,
    factor_version: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FactorProfileSummaryRead]:
    """Aggregate per-factor IC evidence from persisted cross-section profiles."""
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )
    summaries, total = batch_profiler_service.summarize(
        db,
        factor_name=factor_name,
        factor_version=factor_version,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return Page(
        items=[_summary_to_read(summary) for summary in summaries],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/pool-classifications", response_model=list[FactorPoolClassificationRead])
def classify_factor_pool(
    payload: FactorPoolClassificationRequest,
    db: DatabaseSession,
) -> list[FactorPoolClassificationRead]:
    """Classify all active factors into core/observation/frozen pools.

    The classification reads persisted cross-section summaries and applies
    explicit IC/coverage thresholds. Frozen factors should not be used in new
    strategies until their evidence improves.
    """
    if payload.start_date and payload.end_date and payload.start_date > payload.end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    policy = FactorPoolPolicy(
        min_trade_dates=payload.min_trade_dates,
        min_coverage_rate=payload.min_coverage_rate,
        min_rank_ic_1d=payload.min_rank_ic_1d,
        min_rank_ic_5d=payload.min_rank_ic_5d,
        min_rank_ic_20d=payload.min_rank_ic_20d,
        min_long_short_forward_return_1d=payload.min_long_short_forward_return_1d,
    )
    classifier = FactorPoolClassifierService(policy=policy)
    summaries, _ = batch_profiler_service.summarize(
        db,
        start_date=payload.start_date,
        end_date=payload.end_date,
        limit=10000,
        offset=0,
    )
    classifications = classifier.classify(summaries)
    return [_classification_to_read(item) for item in classifications]


def _classification_to_read(
    item: FactorPoolClassification,
) -> FactorPoolClassificationRead:
    return FactorPoolClassificationRead(
        factor_name=item.factor_name,
        factor_version=item.factor_version,
        category=item.category,
        pool=item.pool,
        reasons=item.reasons,
        summary=_summary_to_read(item.summary),
    )


@router.get("/runs", response_model=Page[FactorRunRead])
def list_factor_runs(
    db: DatabaseSession,
    factor_name: str | None = None,
    factor_version: str | None = None,
    status_: Literal["running", "success", "failed", "partial"] | None = Query(
        default=None,
        alias="status",
    ),
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FactorRunRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_runs(
        db,
        factor_name=factor_name,
        factor_version=factor_version,
        status=status_,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@router.get("/values", response_model=Page[FactorValueRead])
def list_factor_values(
    db: DatabaseSession,
    factor_name: str | None = None,
    factor_version: str | None = None,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FactorValueRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_values(
        db,
        factor_name=factor_name,
        factor_version=factor_version,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@router.get("/profiles", response_model=Page[FactorProfileRead])
def list_factor_profiles(
    db: DatabaseSession,
    factor_name: str | None = None,
    factor_version: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FactorProfileRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )
    return service.list_profiles(
        db,
        factor_name=factor_name,
        factor_version=factor_version,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
