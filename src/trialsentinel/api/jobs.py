"""Background audit jobs: submit returns at once; one worker runs audits in order.

Jobs are held in memory for fast polling, and every state change is handed to an `on_change`
hook, which the API uses to persist the job to PostgreSQL. Each change carries a rising
`revision`, so a late-arriving older snapshot can never overwrite a newer one."""

import uuid
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from threading import Lock
from typing import Any

from trialsentinel.agents.evidence import Case
from trialsentinel.agents.service import AuditOptions
from trialsentinel.core.logging import get_logger

log = get_logger(__name__)

Runner = Callable[[Case, AuditOptions], dict[str, Any]]
OnChange = Callable[["AuditJob"], None]

ACTIVE = ("queued", "running")
FINISHED = ("succeeded", "failed")


class QueueFull(Exception):
    """Too many audits waiting; the client should retry later."""


@dataclass
class AuditJob:
    audit_id: str
    nct_id: str
    as_of: date
    submitted_at: datetime
    options: dict[str, Any] = field(default_factory=dict)
    key_fingerprint: str | None = None
    status: str = "queued"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    revision: int = 0


def _now() -> datetime:
    return datetime.now(UTC)


class AuditJobs:
    def __init__(
        self,
        runner: Runner,
        *,
        executor: Executor | None = None,
        max_queued: int = 20,
        max_stored: int = 500,
        on_change: OnChange | None = None,
    ) -> None:
        self._runner = runner
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit")
        self._max_queued = max_queued
        self._max_stored = max_stored
        self._on_change = on_change
        self._jobs: dict[str, AuditJob] = {}
        self._lock = Lock()

    def submit(
        self,
        case: Case,
        options: AuditOptions,
        *,
        as_of: date,
        key_fingerprint: str | None = None,
    ) -> AuditJob:
        with self._lock:
            waiting = sum(j.status in ACTIVE for j in self._jobs.values())
            if waiting >= self._max_queued:
                raise QueueFull
            job = AuditJob(
                audit_id=uuid.uuid4().hex,
                nct_id=case.nct_id,
                as_of=as_of,
                submitted_at=_now(),
                options={
                    "prompt_version": options.prompt_version,
                    "judge": options.judge,
                    "judge_prompt_version": options.judge_prompt_version,
                },
                key_fingerprint=key_fingerprint,
            )
            self._jobs[job.audit_id] = job
            self._evict_finished()
            snapshot = replace(job)
        self._notify(snapshot)
        self._executor.submit(self._run, job, case, options)
        return snapshot

    def get(self, audit_id: str) -> AuditJob | None:
        with self._lock:
            job = self._jobs.get(audit_id)
            return replace(job) if job else None

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _update(self, job: AuditJob, **changes: Any) -> AuditJob:
        with self._lock:
            for name, value in changes.items():
                setattr(job, name, value)
            job.revision += 1
            return replace(job)

    def _notify(self, snapshot: AuditJob) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(snapshot)
        except Exception:  # persistence problems must not stop audits
            log.exception("audit_persist_schedule_failed", audit_id=snapshot.audit_id)

    def _run(self, job: AuditJob, case: Case, options: AuditOptions) -> None:
        self._notify(self._update(job, status="running", started_at=_now()))
        log.info("audit_started", audit_id=job.audit_id, nct_id=job.nct_id)
        try:
            result = self._runner(case, options)
            final = self._update(job, status="succeeded", result=result, finished_at=_now())
        except Exception as exc:  # a failed audit must never kill the worker
            log.exception("audit_failed", audit_id=job.audit_id, nct_id=job.nct_id)
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
            final = self._update(job, status="failed", error=error, finished_at=_now())
        self._notify(final)
        log.info("audit_finished", audit_id=job.audit_id, status=final.status)

    def _evict_finished(self) -> None:
        """Drops the oldest finished jobs from memory (they stay in the database)."""
        excess = len(self._jobs) - self._max_stored
        if excess <= 0:
            return
        finished = sorted(
            (j for j in self._jobs.values() if j.status in FINISHED),
            key=lambda j: j.submitted_at,
        )
        for job in finished[:excess]:
            del self._jobs[job.audit_id]
