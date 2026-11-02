"""Background audit jobs: submit returns at once; one worker runs audits in order.

Jobs live in memory, so they are lost on restart and not shared between processes.
That suits a single local GPU; a durable queue and store (Redis/PostgreSQL) come next."""

import uuid
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from threading import Lock
from typing import Any

from trialsentinel.agents.evidence import Case
from trialsentinel.agents.service import AuditOptions
from trialsentinel.core.logging import get_logger

log = get_logger(__name__)

Runner = Callable[[Case, AuditOptions], dict[str, Any]]


class QueueFull(Exception):
    """Too many audits waiting; the client should retry later."""


@dataclass
class AuditJob:
    audit_id: str
    nct_id: str
    as_of: date
    submitted_at: datetime
    status: str = "queued"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


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
    ) -> None:
        self._runner = runner
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="audit")
        self._max_queued = max_queued
        self._max_stored = max_stored
        self._jobs: dict[str, AuditJob] = {}
        self._lock = Lock()

    def submit(self, case: Case, options: AuditOptions, *, as_of: date) -> AuditJob:
        with self._lock:
            waiting = sum(j.status in ("queued", "running") for j in self._jobs.values())
            if waiting >= self._max_queued:
                raise QueueFull
            job = AuditJob(
                audit_id=uuid.uuid4().hex, nct_id=case.nct_id, as_of=as_of, submitted_at=_now()
            )
            self._jobs[job.audit_id] = job
            self._evict_finished()
        self._executor.submit(self._run, job, case, options)
        return job

    def get(self, audit_id: str) -> AuditJob | None:
        with self._lock:
            return self._jobs.get(audit_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job: AuditJob, case: Case, options: AuditOptions) -> None:
        job.status, job.started_at = "running", _now()
        log.info("audit_started", audit_id=job.audit_id, nct_id=job.nct_id)
        try:
            job.result = self._runner(case, options)
            job.status = "succeeded"
        except Exception as exc:  # a failed audit must never kill the worker
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {str(exc)[:300]}"
            log.exception("audit_failed", audit_id=job.audit_id, nct_id=job.nct_id)
        finally:
            job.finished_at = _now()
        log.info("audit_finished", audit_id=job.audit_id, status=job.status)

    def _evict_finished(self) -> None:
        """Drops the oldest finished jobs once more than max_stored are held."""
        excess = len(self._jobs) - self._max_stored
        if excess <= 0:
            return
        finished = sorted(
            (j for j in self._jobs.values() if j.status in ("succeeded", "failed")),
            key=lambda j: j.submitted_at,
        )
        for job in finished[:excess]:
            del self._jobs[job.audit_id]
