"""Idempotent persistence: unchanged records are skipped via content hash."""

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from trialsentinel.db.models import (
    Publication,
    Trial,
    TrialIntervention,
    TrialOutcome,
    TrialPublicationLink,
)
from trialsentinel.ingestion.models import (
    REGISTRY_LINK_SOURCES,
    LinkSource,
    PartialDate,
    PublicationRecord,
    TrialRecord,
    registry_link_source,
)


class UpsertOutcome(StrEnum):
    INSERTED = "inserted"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


def _earliest(value: PartialDate | None) -> Any:
    return value.earliest if value else None


def _precision(value: PartialDate | None) -> str | None:
    return value.precision.value if value else None


def _trial_values(r: TrialRecord) -> dict[str, Any]:
    return {
        "nct_id": r.nct_id,
        "brief_title": r.brief_title,
        "official_title": r.official_title,
        "overall_status": r.overall_status,
        "study_type": r.study_type,
        "phases": r.phases,
        "conditions": r.conditions,
        "lead_sponsor_name": r.lead_sponsor_name,
        "lead_sponsor_class": r.lead_sponsor_class,
        "enrollment_count": r.enrollment_count,
        "start_date": _earliest(r.start_date),
        "start_date_precision": _precision(r.start_date),
        "primary_completion_date": _earliest(r.primary_completion_date),
        "primary_completion_date_precision": _precision(r.primary_completion_date),
        "primary_completion_type": r.primary_completion_type,
        "completion_date": _earliest(r.completion_date),
        "completion_date_precision": _precision(r.completion_date),
        "results_first_submit_date": _earliest(r.results_first_submit_date),
        "results_first_post_date": _earliest(r.results_first_post_date),
        "last_update_post_date": _earliest(r.last_update_post_date),
        "has_results": r.has_results,
        "brief_summary": r.brief_summary,
        "raw_hash": r.raw_hash,
    }


async def upsert_trial(session: AsyncSession, record: TrialRecord) -> UpsertOutcome:
    existing_hash = await session.scalar(
        select(Trial.raw_hash).where(Trial.nct_id == record.nct_id)
    )
    if existing_hash == record.raw_hash:
        return UpsertOutcome.UNCHANGED

    values = _trial_values(record)
    stmt = pg_insert(Trial).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Trial.nct_id],
        set_={key: stmt.excluded[key] for key in values if key != "nct_id"}
        | {"updated_at": func.now()},
    )
    await session.execute(stmt)

    # Registry-owned child rows are replaced wholesale on every change.
    await session.execute(delete(TrialOutcome).where(TrialOutcome.nct_id == record.nct_id))
    await session.execute(
        delete(TrialIntervention).where(TrialIntervention.nct_id == record.nct_id)
    )
    await session.execute(
        delete(TrialPublicationLink).where(
            TrialPublicationLink.nct_id == record.nct_id,
            TrialPublicationLink.link_source.in_(REGISTRY_LINK_SOURCES),
        )
    )
    if record.outcomes:
        await session.execute(
            insert(TrialOutcome),
            [{"nct_id": record.nct_id, **o.model_dump()} for o in record.outcomes],
        )
    if record.interventions:
        await session.execute(
            insert(TrialIntervention),
            [{"nct_id": record.nct_id, **i.model_dump()} for i in record.interventions],
        )
    if record.references:
        await session.execute(
            pg_insert(TrialPublicationLink)
            .values(
                [
                    {
                        "nct_id": record.nct_id,
                        "pmid": ref.pmid,
                        "link_source": registry_link_source(ref.type).value,
                    }
                    for ref in record.references
                ]
            )
            .on_conflict_do_nothing()
        )

    return UpsertOutcome.INSERTED if existing_hash is None else UpsertOutcome.UPDATED


async def add_links(
    session: AsyncSession, nct_id: str, pmids: Sequence[str], source: LinkSource
) -> int:
    """Insert discovered links; returns how many were new."""
    unique = list(dict.fromkeys(pmids))
    if not unique:
        return 0
    stmt = (
        pg_insert(TrialPublicationLink)
        .values([{"nct_id": nct_id, "pmid": p, "link_source": source.value} for p in unique])
        .on_conflict_do_nothing()
        .returning(TrialPublicationLink.pmid)
    )
    result = await session.execute(stmt)
    return len(result.scalars().all())


async def upsert_publication(session: AsyncSession, record: PublicationRecord) -> UpsertOutcome:
    existing_hash = await session.scalar(
        select(Publication.raw_hash).where(Publication.pmid == record.pmid)
    )
    if existing_hash == record.raw_hash:
        return UpsertOutcome.UNCHANGED

    values = {
        "pmid": record.pmid,
        "title": record.title,
        "abstract": record.abstract,
        "journal": record.journal,
        "pub_date": _earliest(record.pub_date),
        "pub_date_precision": _precision(record.pub_date),
        "doi": record.doi,
        "publication_types": record.publication_types,
        "registry_ids": record.registry_ids,
        "raw_hash": record.raw_hash,
    }
    stmt = pg_insert(Publication).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Publication.pmid],
        set_={key: stmt.excluded[key] for key in values if key != "pmid"}
        | {"updated_at": func.now()},
    )
    await session.execute(stmt)
    return UpsertOutcome.INSERTED if existing_hash is None else UpsertOutcome.UPDATED


async def trials_for_linking(
    session: AsyncSession, *, statuses: Sequence[str] | None, limit: int
) -> list[str]:
    stmt = select(Trial.nct_id)
    if statuses:
        stmt = stmt.where(Trial.overall_status.in_([s.upper() for s in statuses]))
    stmt = stmt.order_by(Trial.primary_completion_date.desc().nulls_last(), Trial.nct_id).limit(
        limit
    )
    return list((await session.scalars(stmt)).all())


async def pmids_without_publication(session: AsyncSession) -> list[str]:
    stmt = (
        select(TrialPublicationLink.pmid)
        .outerjoin(Publication, Publication.pmid == TrialPublicationLink.pmid)
        .where(Publication.pmid.is_(None))
        .distinct()
    )
    return sorted((await session.scalars(stmt)).all())
