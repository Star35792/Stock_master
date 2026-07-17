from typing import Literal

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config.settings import settings
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
    redis: HealthComponent


def check_database() -> HealthComponent:
    try:
        with SessionLocal() as db:
            db.execute(text("select 1"))
    except SQLAlchemyError as exc:
        return HealthComponent(status="error", message=str(exc))
    return HealthComponent(status="ok")


def check_redis() -> HealthComponent:
    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
    except RedisError as exc:
        return HealthComponent(status="error", message=str(exc))
    finally:
        if "client" in locals():
            client.close()
    return HealthComponent(status="ok")


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse | JSONResponse:
    database = check_database()
    redis = check_redis()
    response = HealthResponse(
        status="ok" if database.status == "ok" and redis.status == "ok" else "degraded",
        database=database,
        redis=redis,
    )
    if response.status == "degraded":
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(),
        )
    return response
