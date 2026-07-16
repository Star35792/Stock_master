from fastapi import FastAPI

from app.api import api_router
from app.api.routes import health_router
from app.config.settings import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        debug=settings.app_debug,
        version="0.1.0",
        description="Stock Master trusted market data API.",
    )
    app.include_router(health_router)
    app.include_router(api_router)
    return app


app = create_app()
