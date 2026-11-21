"""TrialSentinel API.

uv run uvicorn trialsentinel.api.main:app --port 8000
open http://127.0.0.1:8000/docs
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import Executor, Future
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from trialsentinel.api.jobs import FINISHED, AuditJob, AuditJobs, Runner
from trialsentinel.api.metrics import Metrics
from trialsentinel.api.routes import audits, health, prometheus, ready, trials
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
    metrics = Metrics()

    def observe_finished(job: AuditJob) -> None:
        duration = (
            (job.finished_at - job.started_at).total_seconds()
            if job.started_at and job.finished_at
            else None
        )
        metrics.observe_audit(job.status, duration, job.result)
        trace = (job.result or {}).get("trace") or {}
        log.info(
            "audit_trace",
            audit_id=job.audit_id,
            nct_id=job.nct_id,
            status=job.status,
            duration_s=round(duration, 1) if duration is not None else None,
            llm_calls=trace.get("llm_calls"),
            input_tokens=trace.get("input_tokens"),
            output_tokens=trace.get("output_tokens"),
            retrieval_ms=trace.get("retrieval_ms"),
            decisions=trace.get("decisions"),
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.api_settings = api
        app.state.store = active_store = store or SqlTrialStore()
        if runner is None:
            from trialsentinel.agents.service import AgentRunner

            run = AgentRunner(settings, judge_model=api.judge_model).run
        else:
            run = runner

        try:
            interrupted = await active_store.mark_interrupted()
            if interrupted:
                log.warning("audits_marked_interrupted", count=interrupted)
        except Exception:  # the database may be down at startup; /readyz reports it
            log.exception("mark_interrupted_failed")

        loop = asyncio.get_running_loop()

        def _log_persist_error(future: Future[None]) -> None:
            if not future.cancelled() and future.exception() is not None:
                log.error("audit_persist_failed", error=repr(future.exception()))

        def on_change(job: AuditJob) -> None:
            # Called from the worker thread too: hand the write to the app's event loop.
            future = asyncio.run_coroutine_threadsafe(active_store.save_audit(job), loop)
            future.add_done_callback(_log_persist_error)
            if job.status in FINISHED:
                observe_finished(job)

        app.state.jobs = AuditJobs(
            run,
            executor=executor,
            max_queued=api.max_queued_audits,
            max_stored=api.max_stored_audits,
            on_change=on_change,
        )
        metrics.track_queue(app.state.jobs.active_count)
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
    app.state.metrics = metrics

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        started = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - started
        response.headers[REQUEST_ID_HEADER] = request_id
        # The route template ("/v1/audits/{audit_id}"), never the raw path, keeps labels bounded.
        route = getattr(request.scope.get("route"), "path", "unmatched")
        metrics.observe_request(request.method, route, response.status_code, elapsed)
        log.info(
            "request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round(elapsed * 1000, 1),
        )
        return response

    app.include_router(health.router)
    app.include_router(ready.router)
    app.include_router(prometheus.router)
    app.include_router(trials.router)
    app.include_router(audits.router)
    app.include_router(audits.history)
    return app


app = create_app()
