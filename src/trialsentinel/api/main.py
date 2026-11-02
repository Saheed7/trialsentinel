"""TrialSentinel API.

uv run uvicorn trialsentinel.api.main:app --port 8000
open http://127.0.0.1:8000/docs
"""

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import Executor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from trialsentinel.api.jobs import AuditJobs, Runner
from trialsentinel.api.routes import audits, health, ready, trials
from trialsentinel.api.security import ApiSettings
from trialsentinel.api.store import SqlTrialStore, TrialStore
from trialsentinel.core.config import get_settings
from trialsentinel.core.logging import configure_logging, get_logger

REQUEST_ID_HEADER = "X-Request-ID"


def create_app(
    *,
    store: TrialStore | None = None,
    runner: Runner | None = None,
    executor: Executor | None = None,
    api_settings: ApiSettings | None = None,
) -> FastAPI:
    """Builds the app. Tests inject a fake store, runner and executor; production uses defaults."""
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.env != "local")
    log = get_logger(__name__)
    api = api_settings or ApiSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.api_settings = api
        app.state.store = store or SqlTrialStore()
        if runner is None:
            from trialsentinel.agents.service import AgentRunner

            run = AgentRunner(settings, judge_model=api.judge_model).run
        else:
            run = runner
        app.state.jobs = AuditJobs(
            run,
            executor=executor,
            max_queued=api.max_queued_audits,
            max_stored=api.max_stored_audits,
        )
        if not api.key_hashes():
            log.warning("no_api_keys_configured", hint="protected endpoints will return 503")
        log.info("startup", env=settings.env, version=settings.version)
        yield
        app.state.jobs.shutdown()
        log.info("shutdown")

    app = FastAPI(
        title="TrialSentinel API",
        version=settings.version,
        description="Clinical-trial transparency audits with verified, evidence-cited claims.",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        log.info(
            "request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return response

    app.include_router(health.router)
    app.include_router(ready.router)
    app.include_router(trials.router)
    app.include_router(audits.router)
    return app


app = create_app()
