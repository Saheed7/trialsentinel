"""Persistence for FAERS drug resolution and disproportionality statistics."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from trialsentinel.db.models import FaersDrug, FaersDrugEventStat, TrialDrugLink, TrialIntervention
from trialsentinel.db.repository import trials_for_linking

DRUG_INTERVENTION_TYPES = ("DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT")
_STAT_COLUMNS = (
    "a",
    "n_drug",
    "n_event",
    "n_total",
    "prr",
    "ror",
    "ror_ci_low",
    "ror_ci_high",
    "chi2",
    "is_signal",
)


async def drug_interventions(
    session: AsyncSession, *, statuses: Sequence[str] | None, limit: int
) -> list[tuple[str, str]]:
    nct_ids = await trials_for_linking(session, statuses=statuses, limit=limit)
    if not nct_ids:
        return []
    stmt = (
        select(TrialIntervention.nct_id, TrialIntervention.name)
        .where(
            TrialIntervention.nct_id.in_(nct_ids),
            TrialIntervention.type.in_(DRUG_INTERVENTION_TYPES),
        )
        .order_by(TrialIntervention.nct_id, TrialIntervention.position)
    )
    return [(row.nct_id, row.name) for row in (await session.execute(stmt)).all()]


async def upsert_faers_drug(
    session: AsyncSession, *, term: str, match_field: str | None, total_reports: int
) -> int:
    stmt = pg_insert(FaersDrug).values(
        query_term=term, match_field=match_field, total_reports=total_reports
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[FaersDrug.query_term],
        set_={
            "match_field": stmt.excluded.match_field,
            "total_reports": stmt.excluded.total_reports,
            "resolved_at": func.now(),
        },
    ).returning(FaersDrug.id)
    return int((await session.execute(stmt)).scalar_one())


async def link_trial_drug(
    session: AsyncSession, *, nct_id: str, drug_id: int, intervention_name: str
) -> None:
    await session.execute(
        pg_insert(TrialDrugLink)
        .values(nct_id=nct_id, drug_id=drug_id, intervention_name=intervention_name)
        .on_conflict_do_nothing()
    )


async def upsert_drug_event_stats(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    stmt = pg_insert(FaersDrugEventStat).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=[
            FaersDrugEventStat.drug_id,
            FaersDrugEventStat.reaction_pt,
            FaersDrugEventStat.snapshot_date,
        ],
        set_={col: stmt.excluded[col] for col in _STAT_COLUMNS} | {"computed_at": func.now()},
    )
    await session.execute(stmt)
