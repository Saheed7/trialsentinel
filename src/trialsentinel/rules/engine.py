"""Evaluates deterministic rules over stored trials and persists versioned assessments."""

from collections import Counter
from datetime import date
from itertools import batched
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from trialsentinel.core.logging import get_logger
from trialsentinel.db.models import FdaaaAssessment, Trial, TrialIntervention
from trialsentinel.db.session import get_sessionmaker
from trialsentinel.rules.fdaaa import (
    RULE_VERSION,
    TrialFacts,
    assess_applicability,
    assess_reporting,
    classify,
    latest_possible,
)

log = get_logger(__name__)
_UPSERT_BATCH = 500
_UPDATE_COLUMNS = (
    "applicability",
    "applicability_confidence",
    "reporting_status",
    "standard_deadline",
    "effective_deadline",
    "deadline_extended",
    "days_overdue",
    "days_late",
    "finding",
    "details",
)


class FdaaaSummary(BaseModel):
    as_of: date
    rule_version: int
    trials: int
    funnel: dict[str, int]


async def load_trial_facts(session: AsyncSession) -> list[TrialFacts]:
    type_rows = await session.execute(
        select(
            TrialIntervention.nct_id, func.array_agg(TrialIntervention.type.distinct())
        ).group_by(TrialIntervention.nct_id)
    )
    types = {nct_id: tuple(t for t in arr if t) for nct_id, arr in type_rows.all()}
    trials = (await session.scalars(select(Trial).order_by(Trial.nct_id))).all()
    return [
        TrialFacts(
            nct_id=t.nct_id,
            overall_status=t.overall_status,
            study_type=t.study_type,
            phases=tuple(t.phases or ()),
            intervention_types=types.get(t.nct_id, ()),
            is_fda_regulated_drug=t.is_fda_regulated_drug,
            is_fda_regulated_device=t.is_fda_regulated_device,
            is_us_export=t.is_us_export,
            has_us_site=t.has_us_site,
            start_date_latest=latest_possible(t.start_date, t.start_date_precision),
            primary_completion_latest=latest_possible(
                t.primary_completion_date, t.primary_completion_date_precision
            ),
            primary_completion_type=t.primary_completion_type,
            results_first_submit_date=t.results_first_submit_date,
            disp_first_submit_date=t.disp_first_submit_date,
        )
        for t in trials
    ]


async def evaluate_fdaaa(*, as_of: date) -> FdaaaSummary:
    funnel: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    async with get_sessionmaker()() as session:
        facts = await load_trial_facts(session)
        for f in facts:
            app = assess_applicability(f)
            rep = assess_reporting(f, as_of=as_of)
            finding = classify(app, rep)
            funnel[f"applicability.{app.status}"] += 1
            funnel[f"reporting.{rep.status}"] += 1
            if finding:
                funnel[f"finding.{finding}"] += 1
            rows.append(
                {
                    "nct_id": f.nct_id,
                    "rule_version": RULE_VERSION,
                    "as_of_date": as_of,
                    "applicability": app.status.value,
                    "applicability_confidence": app.confidence.value,
                    "reporting_status": rep.status.value,
                    "standard_deadline": rep.standard_deadline,
                    "effective_deadline": rep.effective_deadline,
                    "deadline_extended": rep.deadline_extended,
                    "days_overdue": rep.days_overdue,
                    "days_late": rep.days_late,
                    "finding": finding.value if finding else None,
                    "details": {
                        "applicability_basis": list(app.basis),
                        "reporting_basis": list(rep.basis),
                    },
                }
            )
        for batch in batched(rows, _UPSERT_BATCH):
            stmt = pg_insert(FdaaaAssessment).values(list(batch))
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    FdaaaAssessment.nct_id,
                    FdaaaAssessment.rule_version,
                    FdaaaAssessment.as_of_date,
                ],
                set_={col: stmt.excluded[col] for col in _UPDATE_COLUMNS}
                | {"evaluated_at": func.now()},
            )
            await session.execute(stmt)
        await session.commit()

    summary = FdaaaSummary(
        as_of=as_of,
        rule_version=RULE_VERSION,
        trials=len(rows),
        funnel=dict(sorted(funnel.items())),
    )
    log.info("fdaaa_evaluated", as_of=str(as_of), trials=len(rows))
    return summary
