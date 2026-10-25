"""Builds retrievable documents from the system of record (PostgreSQL)."""

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from trialsentinel.db.models import Publication, Trial, TrialOutcome, TrialPublicationLink


@dataclass(frozen=True)
class SourceDocument:
    doc_type: str  # "trial" | "publication"
    source_id: str  # NCT ID or PMID
    title: str
    text: str
    nct_ids: tuple[str, ...]
    pub_date: str | None = None


def trial_document(
    nct_id: str, brief_title: str, brief_summary: str | None, primary_outcomes: list[str]
) -> SourceDocument | None:
    parts = [brief_summary or ""]
    if primary_outcomes:
        parts.append("Primary outcomes: " + "; ".join(primary_outcomes) + ".")
    text = " ".join(p for p in parts if p).strip()
    if not text:
        return None
    return SourceDocument("trial", nct_id, brief_title, text, (nct_id,))


def publication_document(
    pmid: str, title: str, abstract: str | None, nct_ids: list[str], pub_date: str | None
) -> SourceDocument | None:
    text = (abstract or title or "").strip()
    if not text:
        return None
    return SourceDocument("publication", pmid, title, text, tuple(sorted(nct_ids)), pub_date)


async def load_documents(session: AsyncSession) -> list[SourceDocument]:
    outcomes: dict[str, list[str]] = defaultdict(list)
    outcome_rows = await session.execute(
        select(TrialOutcome.nct_id, TrialOutcome.measure)
        .where(TrialOutcome.kind == "primary")
        .order_by(TrialOutcome.nct_id, TrialOutcome.position)
    )
    for nct_id, measure in outcome_rows.all():
        outcomes[nct_id].append(measure)

    link_rows = await session.execute(
        select(
            TrialPublicationLink.pmid, func.array_agg(TrialPublicationLink.nct_id.distinct())
        ).group_by(TrialPublicationLink.pmid)
    )
    links = {pmid: list(ncts) for pmid, ncts in link_rows.all()}

    docs: list[SourceDocument] = []
    for nct_id, title, summary in (
        await session.execute(
            select(Trial.nct_id, Trial.brief_title, Trial.brief_summary).order_by(Trial.nct_id)
        )
    ).all():
        if doc := trial_document(nct_id, title, summary, outcomes.get(nct_id, [])):
            docs.append(doc)
    for pmid, title, abstract, pub_date in (
        await session.execute(
            select(
                Publication.pmid, Publication.title, Publication.abstract, Publication.pub_date
            ).order_by(Publication.pmid)
        )
    ).all():
        iso = pub_date.isoformat() if pub_date else None
        if doc := publication_document(pmid, title, abstract, links.get(pmid, []), iso):
            docs.append(doc)
    return docs
