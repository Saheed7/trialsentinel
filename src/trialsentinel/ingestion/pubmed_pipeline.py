"""Links stored trials to PubMed publications, recording the evidence type of each link."""

import time
import uuid
from collections import Counter
from collections.abc import Sequence
from itertools import batched

from pydantic import BaseModel
from sqlalchemy import func, update

from trialsentinel.core.config import get_settings
from trialsentinel.core.logging import get_logger
from trialsentinel.db.models import IngestionRun
from trialsentinel.db.repository import (
    add_links,
    pmids_without_publication,
    trials_for_linking,
    upsert_publication,
)
from trialsentinel.db.session import get_sessionmaker
from trialsentinel.ingestion.http import SourceHTTPClient
from trialsentinel.ingestion.raw_store import LocalRawStore
from trialsentinel.ingestion.sources.pubmed import (
    EFETCH_BATCH_SIZE,
    SOURCE_NAME,
    PubMedClient,
    parse_pubmed_xml,
)

log = get_logger(__name__)


class PubMedLinkSummary(BaseModel):
    run_id: uuid.UUID
    status: str
    trials_searched: int
    links_found: int
    links_new: int
    publications_requested: int
    publications_fetched: int
    inserted: int
    updated: int
    unchanged: int
    raw_uri: str | None
    duration_s: float


async def link_pubmed(
    *, statuses: Sequence[str] | None = ("COMPLETED",), limit: int = 100
) -> PubMedLinkSummary:
    settings = get_settings()
    sessionmaker = get_sessionmaker()
    run_id = uuid.uuid4()
    params = {"statuses": list(statuses or []), "limit": limit}
    counts: Counter[str] = Counter()
    raw_uri: str | None = None
    status, error = "succeeded", None
    started = time.perf_counter()

    async with sessionmaker() as session:
        session.add(IngestionRun(id=run_id, source=SOURCE_NAME, params=params, status="running"))
        await session.commit()
    log.info("pubmed_link_started", run_id=str(run_id), **params)

    try:
        store = LocalRawStore(settings.raw_data_dir)
        async with SourceHTTPClient(
            settings.pubmed_base_url,
            rate_per_sec=settings.pubmed_requests_per_second,
            timeout_s=settings.http_timeout_s,
            max_retries=settings.http_max_retries,
            user_agent=settings.user_agent,
            default_params=settings.ncbi_params(),
        ) as http:
            client = PubMedClient(http)
            with store.open_batch(SOURCE_NAME, str(run_id)) as raw:
                raw_uri = raw.uri
                async with sessionmaker() as session:
                    nct_ids = await trials_for_linking(session, statuses=statuses, limit=limit)
                    for nct_id in nct_ids:
                        found = await client.search_trial(nct_id)
                        raw.write(
                            {
                                "kind": "esearch",
                                "nct_id": nct_id,
                                "results": {src.value: ids for src, ids in found.items()},
                            }
                        )
                        counts["trials_searched"] += 1
                        for source, pmids in found.items():
                            counts["links_found"] += len(pmids)
                            counts["links_new"] += await add_links(session, nct_id, pmids, source)
                        await session.commit()

                    missing = await pmids_without_publication(session)
                    counts["publications_requested"] = len(missing)
                    for batch in batched(missing, EFETCH_BATCH_SIZE):
                        xml_text = await client.fetch_xml(batch)
                        raw.write({"kind": "efetch", "pmids": list(batch), "xml": xml_text})
                        for publication in parse_pubmed_xml(xml_text):
                            counts["publications_fetched"] += 1
                            outcome = await upsert_publication(session, publication)
                            counts[outcome.value] += 1
                        await session.commit()
    except Exception as exc:
        status, error = "failed", type(exc).__name__
        raise
    finally:
        async with sessionmaker() as session:
            await session.execute(
                update(IngestionRun)
                .where(IngestionRun.id == run_id)
                .values(
                    status=status,
                    finished_at=func.now(),
                    fetched=counts["publications_fetched"],
                    inserted=counts["inserted"],
                    updated=counts["updated"],
                    unchanged=counts["unchanged"],
                    stats=dict(counts),
                    raw_uri=raw_uri,
                    error=error,
                )
            )
            await session.commit()

    summary = PubMedLinkSummary(
        run_id=run_id,
        status=status,
        trials_searched=counts["trials_searched"],
        links_found=counts["links_found"],
        links_new=counts["links_new"],
        publications_requested=counts["publications_requested"],
        publications_fetched=counts["publications_fetched"],
        inserted=counts["inserted"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        raw_uri=raw_uri,
        duration_s=round(time.perf_counter() - started, 2),
    )
    log.info("pubmed_link_completed", **summary.model_dump(mode="json"))
    return summary
