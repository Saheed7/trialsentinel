from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from trialsentinel.api.routes import health
from trialsentinel.core.config import get_settings
from trialsentinel.core.logging import configure_logging, get_logger


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.env != "local")
    log = get_logger(__name__)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info("startup", env=settings.env, version=settings.version)
        yield
        log.info("shutdown")

    app = FastAPI(title="TrialSentinel API", version=settings.version, lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
