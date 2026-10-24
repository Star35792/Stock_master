from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, status

from app.api.dependencies import DatabaseSession, PositionsDatabaseSession
from app.schemas.position_book import (
    PositionBookCreateRequest,
    PositionBookHoldingsRead,
    PositionBookRead,
    PositionBookUpdateRequest,
    PositionTradeCreateRequest,
    PositionTradeRead,
)
from app.services.position_book import (
    PositionBookConflict,
    PositionBookNotFound,
    PositionBookService,
    PositionBookValidationError,
    PositionTradeNotFound,
)

router = APIRouter(prefix="/position-books", tags=["position-books"])
service = PositionBookService()


@router.get("", response_model=list[PositionBookRead])
def list_position_books(
    positions_db: PositionsDatabaseSession,
) -> list[PositionBookRead]:
    return service.list_books(positions_db)


@router.post(
    "",
    response_model=PositionBookRead,
    status_code=status.HTTP_201_CREATED,
)
def create_position_book(
    positions_db: PositionsDatabaseSession,
    payload: PositionBookCreateRequest,
) -> PositionBookRead:
    try:
        result = service.create_book(positions_db, payload)
        positions_db.commit()
        return result
    except PositionBookConflict as exc:
        positions_db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PositionBookValidationError as exc:
        positions_db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception:
        positions_db.rollback()
        raise


@router.patch("/{book_id}", response_model=PositionBookRead)
def update_position_book(
    positions_db: PositionsDatabaseSession,
    book_id: Annotated[int, Path(gt=0)],
    payload: PositionBookUpdateRequest,
) -> PositionBookRead:
    try:
        result = service.update_book(positions_db, book_id=book_id, payload=payload)
        positions_db.commit()
        return result
    except PositionBookNotFound as exc:
        positions_db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PositionBookConflict as exc:
        positions_db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PositionBookValidationError as exc:
        positions_db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception:
        positions_db.rollback()
        raise


@router.get("/{book_id}/holdings", response_model=PositionBookHoldingsRead)
def get_position_book_holdings(
    positions_db: PositionsDatabaseSession,
    db: DatabaseSession,
    book_id: Annotated[int, Path(gt=0)],
) -> PositionBookHoldingsRead:
    """Holdings valued at the latest trusted DuckDB market prices."""
    try:
        return service.get_holdings(positions_db, db, book_id=book_id)
    except PositionBookNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{book_id}/trades", response_model=list[PositionTradeRead])
def list_position_trades(
    positions_db: PositionsDatabaseSession,
    book_id: Annotated[int, Path(gt=0)],
) -> list[PositionTradeRead]:
    try:
        return service.list_trades(positions_db, book_id=book_id)
    except PositionBookNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{book_id}/trades",
    response_model=PositionTradeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_position_trade(
    positions_db: PositionsDatabaseSession,
    db: DatabaseSession,
    book_id: Annotated[int, Path(gt=0)],
    payload: PositionTradeCreateRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=100),
    ],
) -> PositionTradeRead:
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key must not be blank",
        )
    try:
        result = service.add_trade(
            positions_db,
            db,
            book_id=book_id,
            payload=payload,
            idempotency_key=normalized_key,
        )
        positions_db.commit()
        return result
    except PositionBookNotFound as exc:
        positions_db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PositionBookConflict as exc:
        positions_db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except PositionBookValidationError as exc:
        positions_db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception:
        positions_db.rollback()
        raise


@router.delete("/{book_id}/trades/{trade_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_position_trade(
    positions_db: PositionsDatabaseSession,
    book_id: Annotated[int, Path(gt=0)],
    trade_id: Annotated[int, Path(gt=0)],
) -> None:
    try:
        service.delete_trade(positions_db, book_id=book_id, trade_id=trade_id)
        positions_db.commit()
    except (PositionBookNotFound, PositionTradeNotFound) as exc:
        positions_db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except PositionBookValidationError as exc:
        positions_db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception:
        positions_db.rollback()
        raise
