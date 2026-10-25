"""Evidence packs: every fact an agent may state, each with a citable ID (E1, E2, ...)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trialsentinel.db.models import FdaaaAssessment, Publication, Trial, TrialPublicationLink
from trialsentinel.retrieval.search import Hit

MAX_PUBLICATIONS = 8
MAX_EXCERPT_CHARS = 600


@dataclass(frozen=True)
class Evidence:
    id: str
    kind: str  # rule | registry | publication | excerpt
    text: str


@dataclass(frozen=True)
class LinkedPublication:
    pmid: str
    sources: tuple[str, ...]
    title: str
    publication_types: tuple[str, ...]
    pub_date: str | None


@dataclass(frozen=True)
class Case:
    nct_id: str
    brief_title: str
    overall_status: str | None
    primary_completion: str | None
    primary_completion_type: str | None
    results_first_submitted: str | None
    has_results: bool
    lead_sponsor: str | None
    sponsor_class: str | None
    as_of: str
    applicability: str
    applicability_confidence: str
    reporting_status: str
    standard_deadline: str | None
    effective_deadline: str | None
    days_overdue: int | None
    days_late: int | None
    finding: str | None
    basis: tuple[str, ...]
    publications: tuple[LinkedPublication, ...]

    @property
    def verdict(self) -> str:
        return self.finding or self.reporting_status


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _na(value: object) -> str:
    return "n/a" if value is None else str(value)


async def load_case(session: AsyncSession, nct_id: str, *, as_of: date) -> Case | None:
    trial = await session.get(Trial, nct_id)
    if trial is None:
        return None
    assessment = await session.scalar(
        select(FdaaaAssessment)
        .where(FdaaaAssessment.nct_id == nct_id, FdaaaAssessment.as_of_date == as_of)
        .order_by(FdaaaAssessment.rule_version.desc())
        .limit(1)
    )
    if assessment is None:
        return None

    rows = (
        await session.execute(
            select(
                TrialPublicationLink.pmid,
                TrialPublicationLink.link_source,
                Publication.title,
                Publication.publication_types,
                Publication.pub_date,
            )
            .join(Publication, Publication.pmid == TrialPublicationLink.pmid)
            .where(TrialPublicationLink.nct_id == nct_id)
            .order_by(TrialPublicationLink.pmid)
        )
    ).all()
    grouped: dict[str, dict[str, Any]] = {}
    for pmid, source, title, types, pub_date in rows:
        entry = grouped.setdefault(
            pmid,
            {"sources": [], "title": title, "types": tuple(types or ()), "date": _iso(pub_date)},
        )
        entry["sources"].append(source)
    publications = tuple(
        LinkedPublication(pmid, tuple(sorted(v["sources"])), v["title"], v["types"], v["date"])
        for pmid, v in grouped.items()
    )

    details = assessment.details or {}
    return Case(
        nct_id=trial.nct_id,
        brief_title=trial.brief_title,
        overall_status=trial.overall_status,
        primary_completion=_iso(trial.primary_completion_date),
        primary_completion_type=trial.primary_completion_type,
        results_first_submitted=_iso(trial.results_first_submit_date),
        has_results=trial.has_results,
        lead_sponsor=trial.lead_sponsor_name,
        sponsor_class=trial.lead_sponsor_class,
        as_of=as_of.isoformat(),
        applicability=assessment.applicability,
        applicability_confidence=assessment.applicability_confidence,
        reporting_status=assessment.reporting_status,
        standard_deadline=_iso(assessment.standard_deadline),
        effective_deadline=_iso(assessment.effective_deadline),
        days_overdue=assessment.days_overdue,
        days_late=assessment.days_late,
        finding=assessment.finding,
        basis=tuple(details.get("applicability_basis", []))
        + tuple(details.get("reporting_basis", [])),
        publications=publications,
    )


def build_evidence(case: Case, excerpts: Sequence[Hit]) -> list[Evidence]:
    items: list[Evidence] = []

    def add(kind: str, text: str) -> None:
        items.append(Evidence(f"E{len(items) + 1}", kind, text))

    add(
        "rule",
        f"FDAAA 801 rule assessment as of {case.as_of}: applicability {case.applicability} "
        f"({case.applicability_confidence} confidence); reporting status {case.reporting_status}; "
        f"standard deadline {_na(case.standard_deadline)}; effective deadline "
        f"{_na(case.effective_deadline)}; days overdue {_na(case.days_overdue)}; "
        f"days late {_na(case.days_late)}. Basis: {'; '.join(case.basis) or 'none recorded'}.",
    )
    add(
        "registry",
        f"Registry record {case.nct_id} ('{case.brief_title}'): overall status "
        f"{_na(case.overall_status)}; primary completion {_na(case.primary_completion)} "
        f"({_na(case.primary_completion_type)}); results first submitted "
        f"{case.results_first_submitted or 'never'}; results posted: "
        f"{'yes' if case.has_results else 'no'}; lead sponsor {_na(case.lead_sponsor)} "
        f"({_na(case.sponsor_class)}).",
    )
    for pub in case.publications[:MAX_PUBLICATIONS]:
        add(
            "publication",
            f"PubMed {pub.pmid} is linked to {case.nct_id} via {', '.join(pub.sources)}; "
            f"publication types: {', '.join(pub.publication_types) or 'unknown'}; "
            f"published {pub.pub_date or 'date unknown'}; title: {pub.title}",
        )
    if not case.publications:
        add("publication", f"No PubMed publication is linked to {case.nct_id} by any route.")
    for hit in excerpts:
        add(
            "excerpt",
            f"Excerpt from {hit.doc_type} {hit.source_id}: {hit.text[:MAX_EXCERPT_CHARS]}",
        )
    return items
