"""Ingestion orchestration with run lineage (who fetched what, when, with which params)."""

import time
import uuid
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel
from sqlalchemy import func, update

from trialsentinel.core.config import get_settings
from trialsentinel.core.logging import get_logger
from trialsentinel.db.models import IngestionRun
from trialsentinel.db.repository import upsert_trial
from trialsentinel.db.session import get_sessionmaker
from trialsentinel.ingestion.http import SourceHTTPClient
from trialsentinel.ingestion.raw_store import LocalRawStore
from trialsentinel.ingestion.sources.clinicaltrials import (
    SOURCE_NAME,
    ClinicalTrialsClient,
    parse_study,
)

log = get_logger(__name__)
COMMIT_EVERY = 100


class IngestionSummary(BaseModel):
    run_id: uuid.UUID
    source: str
    status: str
    fetched: int
    inserted: int
    updated: int
    unchanged: int
    invalid: int
    raw_uri: str | None
    duration_s: float


async def ingest_clinicaltrials(
    *,
    condition: str | None = None,
    intervention: str | None = None,
    sponsor: str | None = None,
    statuses: Sequence[str] | None = None,
    max_studies: int | None = 100,
    page_size: int = 100,
) -> IngestionSummary:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    run_id = uuid.uuid4()
    params = {
        "condition": condition,
        "intervention": intervention,
        "sponsor": sponsor,
        "statuses": list(statuses or []),
        "max_studies": max_studies,
        "page_size": page_size,
    }
    counts: Counter[str] = Counter()
    raw_uri: str | None = None
    status, error = "succeeded", None
    started = time.perf_counter()

    async with sessionmaker() as session:
        session.add(IngestionRun(id=run_id, source=SOURCE_NAME, params=params, status="running"))
        await session.commit()
    log.info("ingestion_started", run_id=str(run_id), source=SOURCE_NAME, **params)

    try:
        store = LocalRawStore(settings.raw_data_dir)
        async with SourceHTTPClient(
            settings.ctgov_base_url,
            rate_per_sec=settings.ctgov_requests_per_second,
            timeout_s=settings.http_timeout_s,
            max_retries=settings.http_max_retries,
            user_agent=settings.user_agent,
        ) as http:
            client = ClinicalTrialsClient(http)
            with store.open_batch(SOURCE_NAME, str(run_id)) as raw:
                raw_uri = raw.uri
                async with sessionmaker() as session:
                    async for study in client.iter_studies(
                        condition=condition,
                        intervention=intervention,
                        sponsor=sponsor,
                        statuses=statuses,
                        page_size=page_size,
                        max_studies=max_studies,
                    ):
                        raw.write(study)
                        counts["fetched"] += 1
                        try:
                            record = parse_study(study)
                        except ValueError as exc:
                            counts["invalid"] += 1
                            log.warning("ctgov_parse_failed", error=str(exc))
                            continue
                        outcome = await upsert_trial(session, record)
                        counts[outcome.value] += 1
                        if counts["fetched"] % COMMIT_EVERY == 0:
                            await session.commit()
                            log.info("ingestion_progress", run_id=str(run_id), **counts)
                    await session.commit()
    except Exception as exc:
        status, error = "failed", repr(exc)
        raise
    finally:
        async with sessionmaker() as session:
            await session.execute(
                update(IngestionRun)
                .where(IngestionRun.id == run_id)
                .values(
                    status=status,
                    finished_at=func.now(),
                    fetched=counts["fetched"],
                    inserted=counts["inserted"],
                    updated=counts["updated"],
                    unchanged=counts["unchanged"],
                    invalid=counts["invalid"],
                    raw_uri=raw_uri,
                    error=error,
                )
            )
            await session.commit()

    summary = IngestionSummary(
        run_id=run_id,
        source=SOURCE_NAME,
        status=status,
        fetched=counts["fetched"],
        inserted=counts["inserted"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        invalid=counts["invalid"],
        raw_uri=raw_uri,
        duration_s=round(time.perf_counter() - started, 2),
    )
    log.info("ingestion_completed", **summary.model_dump(mode="json"))
    return summary
