from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, status

from app.api.dependencies import DatabaseSession
from app.schemas.approval import (
    PaperExecutionApprovalCreateRequest,
    PaperExecutionApprovalRead,
    PaperExecutionApprovalRevokeRequest,
    PaperExecutionCreateRequest,
    PaperExecutionRead,
)
from app.services.paper_approval import (
    PaperApprovalConflict,
    PaperApprovalNotFound,
    PaperApprovalValidationError,
    PaperExecutionApprovalService,
)
from app.services.paper_execution import (
    PaperExecutionConflict,
    PaperExecutionNotFound,
    PaperExecutionService,
)

router = APIRouter(tags=["paper-execution-approvals"])
service = PaperExecutionApprovalService()
execution_service = PaperExecutionService()


@router.post(
    "/risk-evaluations/{risk_check_id}/confirmations",
    response_model=PaperExecutionApprovalRead,
    status_code=status.HTTP_201_CREATED,
)
def create_paper_execution_approval(
    db: DatabaseSession,
    risk_check_id: Annotated[int, Path(gt=0)],
    payload: PaperExecutionApprovalCreateRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=100),
    ],
) -> PaperExecutionApprovalRead:
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key must not be blank",
        )
    try:
        result = service.create(
            db,
            risk_check_id=risk_check_id,
            expected_evaluation_hash=payload.expected_evaluation_hash,
            confirmation_phrase=payload.confirmation_phrase,
            idempotency_key=normalized_key,
        )
        db.commit()
        return result
    except PaperApprovalNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PaperApprovalConflict as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PaperApprovalValidationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        raise


@router.get(
    "/paper-execution-approvals/{approval_id}",
    response_model=PaperExecutionApprovalRead,
)
def get_paper_execution_approval(
    db: DatabaseSession,
    approval_id: Annotated[int, Path(gt=0)],
) -> PaperExecutionApprovalRead:
    try:
        return service.get(db, approval_id=approval_id)
    except PaperApprovalNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/paper-execution-approvals/{approval_id}/revoke",
    response_model=PaperExecutionApprovalRead,
)
def revoke_paper_execution_approval(
    db: DatabaseSession,
    approval_id: Annotated[int, Path(gt=0)],
    payload: PaperExecutionApprovalRevokeRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=100),
    ],
) -> PaperExecutionApprovalRead:
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key must not be blank",
        )
    try:
        result = service.revoke(
            db,
            approval_id=approval_id,
            reason=payload.reason,
            idempotency_key=normalized_key,
        )
        db.commit()
        return result
    except PaperApprovalNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PaperApprovalConflict as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.post(
    "/paper-execution-approvals/{approval_id}/executions",
    response_model=PaperExecutionRead,
    status_code=status.HTTP_201_CREATED,
)
def execute_paper_approval(
    db: DatabaseSession,
    approval_id: Annotated[int, Path(gt=0)],
    payload: PaperExecutionCreateRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=100),
    ],
) -> PaperExecutionRead:
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key must not be blank",
        )
    try:
        result = execution_service.execute(
            db,
            approval_id=approval_id,
            expected_approval_hash=payload.expected_approval_hash,
            idempotency_key=normalized_key,
        )
        db.commit()
        return result
    except PaperExecutionNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PaperExecutionConflict as exc:
        db.rollback()
        detail: str | dict[str, str | int] = str(exc)
        if exc.existing_execution_id is not None:
            detail = {
                "message": str(exc),
                "existing_execution_id": exc.existing_execution_id,
            }
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
    except Exception:
        db.rollback()
        raise


@router.get(
    "/paper-executions/{execution_id}",
    response_model=PaperExecutionRead,
)
def get_paper_execution(
    db: DatabaseSession,
    execution_id: Annotated[int, Path(gt=0)],
) -> PaperExecutionRead:
    try:
        return execution_service.get(db, execution_id=execution_id)
    except PaperExecutionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/paper-execution-approvals/{approval_id}/execution",
    response_model=PaperExecutionRead,
)
def get_paper_execution_by_approval(
    db: DatabaseSession,
    approval_id: Annotated[int, Path(gt=0)],
) -> PaperExecutionRead:
    try:
        return execution_service.get_by_approval(db, approval_id=approval_id)
    except PaperExecutionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
