"""CELINE Grid BFF — FastAPI application factory."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from celine.grid.db import init_db
from celine.grid.routes import create_api_router
from celine.grid.security.middleware import PolicyMiddleware
from celine.grid.services.pipeline_listener import create_broker, on_pipeline_run
from celine.grid.settings import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    broker = create_broker()
    try:
        await broker.connect()
        await broker.subscribe(["celine/pipelines/runs/+"], on_pipeline_run)
        logger.info("MQTT pipeline listener subscribed")
    except Exception as exc:
        logger.warning("MQTT broker unavailable at startup: %s", exc)
        await broker.disconnect()

    yield

    try:
        await broker.disconnect()
    except Exception:
        pass


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(name)s  %(message)s")

    app = FastAPI(
        title="CELINE Grid BFF",
        description="Backend-for-frontend for the Grid Resilience UI",
        version="0.1.0",
        lifespan=lifespan,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(PolicyMiddleware)

    @app.get("/health", tags=["ops"])
    async def health() -> dict:
        return {"status": "ok"}

    app.include_router(create_api_router())

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "celine.grid.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
