"""Read access to trials and rule assessments for the API.

A small interface (TrialStore) keeps the routes independent of the database, so tests run
against an in-memory fake and the real store runs on the app's own event loop."""

from datetime import date
from typing import Protocol

from sqlalchemy import select, text

from trialsentinel.agents.evidence import Case, load_case
from trialsentinel.api.schemas import Assessment, Finding
from trialsentinel.db.models import FdaaaAssessment
from trialsentinel.db.session import get_sessionmaker


class TrialStore(Protocol):
    async def ping(self) -> None: ...
    async def findings(self, as_of: date) -> list[Finding]: ...
    async def assessment(self, nct_id: str, as_of: date) -> Assessment | None: ...
    async def case(self, nct_id: str, as_of: date) -> Case | None: ...


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
