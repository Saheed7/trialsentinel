"""Known-item retrieval evaluation. Labels come free from link provenance: the papers
indexed as reporting a trial should be retrieved when querying with that trial's title."""

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from trialsentinel.db.models import Publication, Trial, TrialPublicationLink
from trialsentinel.retrieval.search import HybridRetriever, Mode

STRONG_SOURCES = ("registry_result", "pubmed_si")


@dataclass(frozen=True)
class KnownItem:
    nct_id: str
    query: str
    relevant: frozenset[str]


async def load_known_items(session: AsyncSession) -> list[KnownItem]:
    stmt = (
        select(
            Trial.nct_id, Trial.brief_title, func.array_agg(TrialPublicationLink.pmid.distinct())
        )
        .join(TrialPublicationLink, TrialPublicationLink.nct_id == Trial.nct_id)
        .join(Publication, Publication.pmid == TrialPublicationLink.pmid)
        .where(TrialPublicationLink.link_source.in_(STRONG_SOURCES), Trial.brief_title != "")
        .group_by(Trial.nct_id, Trial.brief_title)
        .order_by(Trial.nct_id)
    )
    rows = (await session.execute(stmt)).all()
    return [KnownItem(nct_id, title, frozenset(pmids)) for nct_id, title, pmids in rows]


def unique_sources(source_ids: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(source_ids))


def score_ranking(
    ranked: Sequence[str], relevant: frozenset[str], *, k: int
) -> tuple[float, float]:
    """Returns (hit@k, reciprocal rank within the top 10)."""
    hit = 1.0 if any(r in relevant for r in ranked[:k]) else 0.0
    rr = next((1.0 / (i + 1) for i, r in enumerate(ranked[:10]) if r in relevant), 0.0)
    return hit, rr


def evaluate(
    retriever: HybridRetriever, items: Sequence[KnownItem], modes: Sequence[Mode], *, k: int = 5
) -> dict[str, dict[str, float]]:
    if not items:
        raise ValueError("no known items: link trials to PubMed and index first")
    report: dict[str, dict[str, float]] = {}
    for mode in modes:
        at_1, at_k, rrs, latencies = [], [], [], []
        for item in items:
            started = time.perf_counter()
            hits = retriever.search(item.query, limit=20, mode=mode, doc_type="publication")
            latencies.append(time.perf_counter() - started)
            ranked = unique_sources(h.source_id for h in hits)
            at_1.append(score_ranking(ranked, item.relevant, k=1)[0])
            hit_k, rr = score_ranking(ranked, item.relevant, k=k)
            at_k.append(hit_k)
            rrs.append(rr)
        report[mode] = {
            "queries": len(items),
            "hit@1": round(mean(at_1), 3),
            f"hit@{k}": round(mean(at_k), 3),
            "mrr@10": round(mean(rrs), 3),
            "mean_latency_ms": round(1000 * mean(latencies), 1),
        }
    return report
