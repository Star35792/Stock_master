from fastapi import APIRouter

from app.api.routes import (
    adjustment_factors_router,
    approvals_router,
    backtests_router,
    corporate_actions_router,
    daily_bars_router,
    data_quality_router,
    factors_router,
    manual_corrections_router,
    operations_router,
    paper_accounts_router,
    portfolio_router,
    securities_router,
    source_compares_router,
    strategy_research_router,
    trading_calendar_router,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(securities_router)
api_router.include_router(approvals_router)
api_router.include_router(daily_bars_router)
api_router.include_router(data_quality_router)
api_router.include_router(factors_router)
api_router.include_router(backtests_router)
api_router.include_router(strategy_research_router)
api_router.include_router(source_compares_router)
api_router.include_router(manual_corrections_router)
api_router.include_router(corporate_actions_router)
api_router.include_router(adjustment_factors_router)
api_router.include_router(trading_calendar_router)
api_router.include_router(operations_router)
api_router.include_router(paper_accounts_router)
api_router.include_router(portfolio_router)
