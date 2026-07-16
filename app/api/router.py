from fastapi import APIRouter

from app.api.routes import daily_bars_router, data_quality_router, securities_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(securities_router)
api_router.include_router(daily_bars_router)
api_router.include_router(data_quality_router)
