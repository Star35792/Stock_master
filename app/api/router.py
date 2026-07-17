from fastapi import APIRouter

from app.api.routes import (
    adjustment_factors_router,
    corporate_actions_router,
    daily_bars_router,
    data_quality_router,
    factors_router,
    manual_corrections_router,
    securities_router,
    source_compares_router,
    trading_calendar_router,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(securities_router)
api_router.include_router(daily_bars_router)
api_router.include_router(data_quality_router)
api_router.include_router(factors_router)
api_router.include_router(source_compares_router)
api_router.include_router(manual_corrections_router)
api_router.include_router(corporate_actions_router)
api_router.include_router(adjustment_factors_router)
api_router.include_router(trading_calendar_router)
