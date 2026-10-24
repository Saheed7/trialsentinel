"""Re-fetches every stored trial by NCT ID, so parser upgrades reach all records.

Search queries return different studies over time; re-syncing what we already hold
must not depend on them.
"""

import uuid

import httpx
from pydantic import BaseModel
from sqlalchemy import select

from trialsentinel.core.config import get_settings
from trialsentinel.db.models import Trial
from trialsentinel.db.repository import upsert_trial
from trialsentinel.db.session import get_sessionmaker
from trialsentinel.ingestion.http import SourceHTTPClient
from trialsentinel.ingestion.raw_store import LocalRawStore
from trialsentinel.ingestion.runs import RunTracker
from trialsentinel.ingestion.sources.clinicaltrials import (
    SOURCE_NAME,
    ClinicalTrialsClient,
    parse_study,
)

COMMIT_EVERY = 50


class RefreshSummary(BaseModel):
    run_id: uuid.UUID
    status: str
    requested: int
    fetched: int
    updated: int
    unchanged: int
    invalid: int
    not_found: int
    duration_s: float


async def refresh_clinicaltrials(*, limit: int | None = None) -> RefreshSummary:
    settings = get_settings()
    tracker = RunTracker("ctgov_refresh", {"limit": limit})
    async with tracker as run:
        async with get_sessionmaker()() as session:
            stmt = select(Trial.nct_id).order_by(Trial.nct_id)
            if limit:
                stmt = stmt.limit(limit)
            nct_ids = list((await session.scalars(stmt)).all())
        run.counts["requested"] = len(nct_ids)

        async with SourceHTTPClient(
            settings.ctgov_base_url,
            rate_per_sec=settings.ctgov_requests_per_second,
            timeout_s=settings.http_timeout_s,
            max_retries=settings.http_max_retries,
            user_agent=settings.user_agent,
        ) as http:
            client = ClinicalTrialsClient(http)
            store = LocalRawStore(settings.raw_data_dir)
            with store.open_batch(SOURCE_NAME, str(run.run_id)) as raw:
                run.raw_uri = raw.uri
                async with get_sessionmaker()() as session:
                    for i, nct_id in enumerate(nct_ids, start=1):
                        try:
                            study = await client.get_study(nct_id)
                        except httpx.HTTPStatusError as exc:
                            if exc.response.status_code == 404:  # record removed upstream
                                run.counts["not_found"] += 1
                                continue
                            raise
                        raw.write(study)
                        run.counts["fetched"] += 1
                        try:
                            record = parse_study(study)
                        except ValueError:
                            run.counts["invalid"] += 1
                            continue
                        outcome = await upsert_trial(session, record)
                        run.counts[outcome.value] += 1
                        if i % COMMIT_EVERY == 0:
                            await session.commit()
                    await session.commit()

    c = tracker.counts
    return RefreshSummary(
        run_id=tracker.run_id,
        status=tracker.status,
        requested=c["requested"],
        fetched=c["fetched"],
        updated=c["updated"],
        unchanged=c["unchanged"],
        invalid=c["invalid"],
        not_found=c["not_found"],
        duration_s=tracker.duration_s,
    )
