from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.api.routes import health_router
from app.config.settings import settings
from app.database.positions_session import initialize_positions_database
from app.database.session import initialize_database
from app.scheduler.auto_data_sync import AutoDataSyncWorker
from app.scheduler.price_value_input_prep import PriceValueInputPrepWorker
from app.scheduler.price_value_screen import PriceValueScreenWorker


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    initialize_positions_database()
    worker = AutoDataSyncWorker(settings)
    input_worker = PriceValueInputPrepWorker(settings)
    screen_worker = PriceValueScreenWorker(settings)
    app.state.auto_data_sync = worker
    app.state.price_value_input_prep = input_worker
    app.state.price_value_screen = screen_worker
    worker.start()
    input_worker.start()
    screen_worker.start()
    try:
        yield
    finally:
        await screen_worker.stop()
        await input_worker.stop()
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
