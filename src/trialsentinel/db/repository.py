"""Idempotent persistence: unchanged records are skipped via content hash."""

from enum import StrEnum
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from trialsentinel.db.models import Trial, TrialIntervention, TrialOutcome
from trialsentinel.ingestion.models import PartialDate, TrialRecord


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

    # Child rows are replaced wholesale: simpler and correct for registry amendments.
    await session.execute(delete(TrialOutcome).where(TrialOutcome.nct_id == record.nct_id))
    await session.execute(
        delete(TrialIntervention).where(TrialIntervention.nct_id == record.nct_id)
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

    return UpsertOutcome.INSERTED if existing_hash is None else UpsertOutcome.UPDATED
