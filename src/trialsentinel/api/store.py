"""Database access for the API: rule-engine reads and durable audit records.

A small interface (TrialStore) keeps the routes independent of the database, so tests run
against an in-memory fake, and the real store always runs on the app's own event loop."""

import uuid
from datetime import UTC, date, datetime
from typing import Protocol

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import Insert
from sqlalchemy.dialects.postgresql import insert as pg_insert

from trialsentinel.agents.evidence import Case, load_case
from trialsentinel.api.jobs import ACTIVE, AuditJob
from trialsentinel.api.schemas import Assessment, Finding
from trialsentinel.db.models import AgentAudit, FdaaaAssessment
from trialsentinel.db.session import get_sessionmaker

INTERRUPTED = "interrupted: the service restarted before this audit finished"


class TrialStore(Protocol):
    async def ping(self) -> None: ...
    async def findings(self, as_of: date) -> list[Finding]: ...
    async def assessment(self, nct_id: str, as_of: date) -> Assessment | None: ...
    async def case(self, nct_id: str, as_of: date) -> Case | None: ...
    async def save_audit(self, job: AuditJob) -> None: ...
    async def load_audit(self, audit_id: str) -> AuditJob | None: ...
    async def list_audits(self, nct_id: str, limit: int) -> list[AuditJob]: ...
    async def mark_interrupted(self) -> int: ...


def _to_assessment(row: FdaaaAssessment) -> Assessment:
    details = row.details or {}
    basis = [*details.get("applicability_basis", []), *details.get("reporting_basis", [])]
    return Assessment(
        nct_id=row.nct_id,
        as_of=row.as_of_date,
        rule_version=str(row.rule_version),
        applicability=row.applicability,
        applicability_confidence=row.applicability_confidence,
        reporting_status=row.reporting_status,
        standard_deadline=row.standard_deadline,
        effective_deadline=row.effective_deadline,
        days_overdue=row.days_overdue,
        days_late=row.days_late,
        finding=row.finding,
        basis=basis,
    )


def _audit_values(job: AuditJob) -> dict[str, object]:
    return {
        "id": uuid.UUID(hex=job.audit_id),
        "nct_id": job.nct_id,
        "as_of_date": job.as_of,
        "status": job.status,
        "options": job.options,
        "key_fingerprint": job.key_fingerprint,
        "verifier_version": (job.result or {}).get("verifier_version"),
        "result": job.result,
        "error": job.error,
        "revision": job.revision,
        "submitted_at": job.submitted_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def audit_upsert(job: AuditJob) -> Insert:
    """Insert, or update only if this snapshot is newer than the stored one.

    Writes can arrive out of order (they are scheduled from the worker thread), so the
    revision guard stops a stale 'running' snapshot overwriting a finished audit."""
    values = _audit_values(job)
    stmt = pg_insert(AgentAudit).values(**values)
    return stmt.on_conflict_do_update(
        index_elements=[AgentAudit.id],
        set_={k: stmt.excluded[k] for k in values if k != "id"},
        where=AgentAudit.revision < stmt.excluded.revision,
    )


def _to_job(row: AgentAudit) -> AuditJob:
    return AuditJob(
        audit_id=row.id.hex,
        nct_id=row.nct_id,
        as_of=row.as_of_date,
        submitted_at=row.submitted_at,
        options=row.options or {},
        key_fingerprint=row.key_fingerprint,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        result=row.result,
        error=row.error,
        revision=row.revision,
    )


class SqlTrialStore:
    async def ping(self) -> None:
        async with get_sessionmaker()() as session:
            await session.execute(text("SELECT 1"))

    async def findings(self, as_of: date) -> list[Finding]:
        stmt = (
            select(FdaaaAssessment)
            .where(FdaaaAssessment.as_of_date == as_of, FdaaaAssessment.finding.is_not(None))
            .order_by(FdaaaAssessment.nct_id, FdaaaAssessment.rule_version.desc())
        )
        async with get_sessionmaker()() as session:
            rows = (await session.scalars(stmt)).all()
        latest: dict[str, Finding] = {}
        for row in rows:  # newest rule version first, so the first row per trial wins
            latest.setdefault(
                row.nct_id,
                Finding(
                    nct_id=row.nct_id,
                    finding=row.finding,
                    reporting_status=row.reporting_status,
                    days_overdue=row.days_overdue,
                    days_late=row.days_late,
                ),
            )
        return list(latest.values())

    async def assessment(self, nct_id: str, as_of: date) -> Assessment | None:
        stmt = (
            select(FdaaaAssessment)
            .where(FdaaaAssessment.nct_id == nct_id, FdaaaAssessment.as_of_date == as_of)
            .order_by(FdaaaAssessment.rule_version.desc())
            .limit(1)
        )
        async with get_sessionmaker()() as session:
            row = await session.scalar(stmt)
        return _to_assessment(row) if row else None

    async def case(self, nct_id: str, as_of: date) -> Case | None:
        async with get_sessionmaker()() as session:
            return await load_case(session, nct_id, as_of=as_of)

    async def save_audit(self, job: AuditJob) -> None:
        async with get_sessionmaker()() as session:
            await session.execute(audit_upsert(job))
            await session.commit()

    async def load_audit(self, audit_id: str) -> AuditJob | None:
        async with get_sessionmaker()() as session:
            row = await session.get(AgentAudit, uuid.UUID(hex=audit_id))
        return _to_job(row) if row else None

    async def list_audits(self, nct_id: str, limit: int) -> list[AuditJob]:
        stmt = (
            select(AgentAudit)
            .where(AgentAudit.nct_id == nct_id)
            .order_by(AgentAudit.submitted_at.desc())
            .limit(limit)
        )
        async with get_sessionmaker()() as session:
            rows = (await session.scalars(stmt)).all()
        return [_to_job(r) for r in rows]

    async def mark_interrupted(self) -> int:
        """At startup: audits left queued or running by a previous process can never finish."""
        stmt = (
            update(AgentAudit)
            .where(AgentAudit.status.in_(ACTIVE))
            .values(
                status="failed",
                error=INTERRUPTED,
                finished_at=datetime.now(UTC),
                revision=AgentAudit.revision + 1,
            )
        )
        async with get_sessionmaker()() as session:
            result = await session.execute(stmt)
            await session.commit()
        return result.rowcount or 0
