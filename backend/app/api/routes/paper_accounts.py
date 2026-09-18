from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, status

from app.api.dependencies import DatabaseSession
from app.schemas.trade import (
    PaperAccountRead,
    PaperEquitySnapshotCreateRequest,
    PaperEquitySnapshotRead,
    PaperLedgerAuditRead,
)
from app.services.paper_equity import (
    PaperAccountNotFound,
    PaperEquityConflict,
    PaperEquitySnapshotNotFound,
    PaperEquitySnapshotService,
    PaperEquityValidationError,
)
from app.services.paper_ledger_audit import (
    PaperLedgerAccountNotFound,
    PaperLedgerAuditService,
)

router = APIRouter(prefix="/paper-accounts", tags=["paper-accounts"])
service = PaperEquitySnapshotService()
audit_service = PaperLedgerAuditService()


@router.get("", response_model=list[PaperAccountRead])
def list_paper_accounts(db: DatabaseSession) -> list[PaperAccountRead]:
    return service.list_accounts(db)


@router.get(
    "/{account_id}/ledger-audit",
    response_model=PaperLedgerAuditRead,
)
def get_paper_ledger_audit(
    db: DatabaseSession,
    account_id: Annotated[int, Path(gt=0)],
) -> PaperLedgerAuditRead:
    try:
        return audit_service.audit_account(db, account_id=account_id)
    except PaperLedgerAccountNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{account_id}/equity-snapshots",
    response_model=PaperEquitySnapshotRead,
    status_code=status.HTTP_201_CREATED,
)
def capture_paper_equity_snapshot(
    db: DatabaseSession,
    account_id: Annotated[int, Path(gt=0)],
    payload: PaperEquitySnapshotCreateRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=100),
    ],
) -> PaperEquitySnapshotRead:
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key must not be blank",
        )
    try:
        result = service.capture(
            db,
            account_id=account_id,
            valuation_date=payload.valuation_date,
            idempotency_key=normalized_key,
        )
        db.commit()
        return result
    except PaperAccountNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PaperEquityConflict as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PaperEquityValidationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        raise


@router.get(
    "/{account_id}/equity-snapshots/{snapshot_id}",
    response_model=PaperEquitySnapshotRead,
)
def get_paper_equity_snapshot(
    db: DatabaseSession,
    account_id: Annotated[int, Path(gt=0)],
    snapshot_id: Annotated[int, Path(gt=0)],
) -> PaperEquitySnapshotRead:
    try:
        return service.get_snapshot(
            db,
            account_id=account_id,
            snapshot_id=snapshot_id,
        )
    except PaperEquitySnapshotNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
