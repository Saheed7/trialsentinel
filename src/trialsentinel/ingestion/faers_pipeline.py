"""Resolves trial drugs in FAERS and computes drug-event disproportionality."""

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime

from pydantic import BaseModel

from trialsentinel.core.config import get_settings
from trialsentinel.db.faers_repository import (
    drug_interventions,
    link_trial_drug,
    upsert_drug_event_stats,
    upsert_faers_drug,
)
from trialsentinel.db.session import get_sessionmaker
from trialsentinel.ingestion.http import SourceHTTPClient
from trialsentinel.ingestion.raw_store import LocalRawStore
from trialsentinel.ingestion.runs import RunTracker
from trialsentinel.ingestion.sources.openfda import (
    SOURCE_NAME,
    OpenFDAClient,
    drug_search,
    normalize_drug_terms,
    reaction_search,
)
from trialsentinel.rules.disproportionality import compute_disproportionality


class FaersSummary(BaseModel):
    run_id: uuid.UUID
    status: str
    snapshot_date: date | None
    distinct_terms: int
    drugs_queried: int
    drugs_resolved: int
    drug_event_pairs: int
    signals: int
    raw_uri: str | None
    duration_s: float


def _snapshot(last_updated: str | None) -> date:
    try:
        return date.fromisoformat(last_updated or "")
    except ValueError:
        return datetime.now(UTC).date()


async def _resolve(client: OpenFDAClient, term: str) -> tuple[str, int] | None:
    for field in ("generic_name", "brand_name", "medicinalproduct"):
        total, _ = await client.total_reports(drug_search(term, field))
        if total > 0:
            return field, total
    return None


async def ingest_faers(
    *,
    statuses: Sequence[str] | None = ("COMPLETED",),
    trial_limit: int = 75,
    max_drugs: int = 10,
    top_events: int = 25,
) -> FaersSummary:
    settings = get_settings()
    params = {
        "statuses": list(statuses or []),
        "trial_limit": trial_limit,
        "max_drugs": max_drugs,
        "top_events": top_events,
    }
    snapshot: date | None = None
    tracker = RunTracker(SOURCE_NAME, params)

    async with (
        tracker as run,
        SourceHTTPClient(
            settings.openfda_base_url,
            rate_per_sec=settings.openfda_requests_per_second,
            timeout_s=settings.http_timeout_s,
            max_retries=settings.http_max_retries,
            user_agent=settings.user_agent,
            default_params=settings.openfda_params(),
        ) as http,
    ):
        client = OpenFDAClient(http)
        store = LocalRawStore(settings.raw_data_dir)
        with store.open_batch(SOURCE_NAME, str(run.run_id)) as raw:
            run.raw_uri = raw.uri
            async with get_sessionmaker()() as session:
                uses_by_term: dict[str, list[tuple[str, str]]] = {}
                interventions = await drug_interventions(
                    session, statuses=statuses, limit=trial_limit
                )
                for nct_id, name in interventions:
                    terms = normalize_drug_terms(name)
                    if not terms:
                        run.counts["interventions_skipped"] += 1
                    for term in terms:
                        uses_by_term.setdefault(term, []).append((nct_id, name))
                run.counts["distinct_terms"] = len(uses_by_term)

                n_total, last_updated = await client.total_reports()
                snapshot = _snapshot(last_updated)
                event_totals: dict[str, int] = {}  # in-run cache; Redis comes in Phase 5

                for term, uses in list(uses_by_term.items())[:max_drugs]:
                    run.counts["drugs_queried"] += 1
                    resolved = await _resolve(client, term)
                    drug_id = await upsert_faers_drug(
                        session,
                        term=term,
                        match_field=resolved[0] if resolved else None,
                        total_reports=resolved[1] if resolved else 0,
                    )
                    for nct_id, name in uses:
                        await link_trial_drug(
                            session, nct_id=nct_id, drug_id=drug_id, intervention_name=name
                        )
                    if resolved is None:
                        raw.write({"kind": "resolve", "term": term, "resolved": False})
                        await session.commit()
                        continue

                    field, n_drug = resolved
                    run.counts["drugs_resolved"] += 1
                    reactions = await client.top_reactions(
                        drug_search(term, field), limit=top_events
                    )
                    rows = []
                    for pt, a in reactions:
                        if pt not in event_totals:
                            event_totals[pt], _ = await client.total_reports(reaction_search(pt))
                        try:
                            stats = compute_disproportionality(
                                a=a, n_drug=n_drug, n_event=event_totals[pt], n_total=n_total
                            )
                        except ValueError:
                            run.counts["inconsistent_pairs"] += 1
                            continue
                        rows.append(
                            {
                                "drug_id": drug_id,
                                "reaction_pt": pt,
                                "snapshot_date": snapshot,
                                "a": a,
                                "n_drug": n_drug,
                                "n_event": event_totals[pt],
                                "n_total": n_total,
                                "prr": stats.prr,
                                "ror": stats.ror,
                                "ror_ci_low": stats.ror_ci_low,
                                "ror_ci_high": stats.ror_ci_high,
                                "chi2": stats.chi2,
                                "is_signal": stats.is_signal,
                            }
                        )
                        run.counts["signals"] += int(stats.is_signal)
                    raw.write(
                        {
                            "kind": "reactions",
                            "term": term,
                            "field": field,
                            "n_drug": n_drug,
                            "n_total": n_total,
                            "reactions": reactions,
                            "event_totals": {pt: event_totals.get(pt) for pt, _ in reactions},
                        }
                    )
                    await upsert_drug_event_stats(session, rows)
                    run.counts["drug_event_pairs"] += len(rows)
                    run.counts["fetched"] += len(rows)
                    await session.commit()

    return FaersSummary(
        run_id=tracker.run_id,
        status=tracker.status,
        snapshot_date=snapshot,
        distinct_terms=tracker.counts["distinct_terms"],
        drugs_queried=tracker.counts["drugs_queried"],
        drugs_resolved=tracker.counts["drugs_resolved"],
        drug_event_pairs=tracker.counts["drug_event_pairs"],
        signals=tracker.counts["signals"],
        raw_uri=tracker.raw_uri,
        duration_s=tracker.duration_s,
    )
