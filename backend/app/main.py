from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.api.routes import health_router
from app.config.settings import settings
from app.database.session import initialize_database
from app.scheduler.auto_data_sync import AutoDataSyncWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    worker = AutoDataSyncWorker(settings)
    app.state.auto_data_sync = worker
    worker.start()
    try:
        yield
    finally:
        await worker.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        debug=settings.app_debug,
        version="0.1.0",
        description="Stock Master trusted market data API.",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(api_router)
    return app


app = create_app()
