from typing import Literal

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.database.session import SessionLocal

router = APIRouter(tags=["health"])

ComponentStatus = Literal["ok", "error"]
HealthStatus = Literal["ok", "degraded"]


class HealthComponent(BaseModel):
    status: ComponentStatus
    message: str | None = None


class HealthResponse(BaseModel):
    status: HealthStatus
    database: HealthComponent


def check_database() -> HealthComponent:
    try:
        with SessionLocal() as db:
            db.execute(text("select 1"))
    except SQLAlchemyError as exc:
        return HealthComponent(status="error", message=str(exc))
    return HealthComponent(status="ok", message="duckdb")


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse | JSONResponse:
    database = check_database()
    response = HealthResponse(
        status="ok" if database.status == "ok" else "degraded",
        database=database,
    )
    if response.status == "degraded":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(),
        )
    return response
