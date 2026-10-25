"""Hybrid retrieval: dense + sparse prefetch fused with RRF, optional cross-encoder rerank."""

from dataclasses import dataclass, replace
from typing import Any, Literal

from qdrant_client import QdrantClient, models

from trialsentinel.retrieval.embeddings import Embedder
from trialsentinel.retrieval.index import DENSE, SPARSE

Mode = Literal["dense", "sparse", "hybrid", "hybrid_rerank"]
MODES: tuple[Mode, ...] = ("dense", "sparse", "hybrid", "hybrid_rerank")


@dataclass(frozen=True)
class Hit:
    point_id: str
    score: float
    doc_type: str
    source_id: str
    title: str
    text: str
    nct_ids: tuple[str, ...]


def build_filter(*, doc_type: str | None = None, nct_id: str | None = None) -> models.Filter | None:
    must: list[Any] = []
    if doc_type:
        must.append(models.FieldCondition(key="doc_type", match=models.MatchValue(value=doc_type)))
    if nct_id:
        must.append(models.FieldCondition(key="nct_ids", match=models.MatchValue(value=nct_id)))
    return models.Filter(must=must) if must else None


def _to_hit(point: models.ScoredPoint) -> Hit:
    p = point.payload or {}
    return Hit(
        point_id=str(point.id),
        score=float(point.score),
        doc_type=p.get("doc_type", ""),
        source_id=p.get("source_id", ""),
        title=p.get("title", ""),
        text=p.get("text", ""),
        nct_ids=tuple(p.get("nct_ids", [])),
    )


class HybridRetriever:
    def __init__(
        self, client: QdrantClient, collection: str, embedder: Embedder, *, candidates: int = 50
    ) -> None:
        self._client = client
        self._collection = collection
        self._embedder = embedder
        self._candidates = candidates

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        mode: Mode = "hybrid",
        doc_type: str | None = None,
        nct_id: str | None = None,
    ) -> list[Hit]:
        flt = build_filter(doc_type=doc_type, nct_id=nct_id)
        fetch = self._candidates if mode == "hybrid_rerank" else limit
        if mode == "dense":
            response = self._client.query_points(
                self._collection,
                query=self._embedder.query_dense(query),
                using=DENSE,
                query_filter=flt,
                limit=fetch,
                with_payload=True,
            )
        elif mode == "sparse":
            response = self._client.query_points(
                self._collection,
                query=self._embedder.query_sparse(query),
                using=SPARSE,
                query_filter=flt,
                limit=fetch,
                with_payload=True,
            )
        else:
            response = self._client.query_points(
                self._collection,
                prefetch=[
                    models.Prefetch(
                        query=self._embedder.query_dense(query),
                        using=DENSE,
                        filter=flt,
                        limit=self._candidates,
                    ),
                    models.Prefetch(
                        query=self._embedder.query_sparse(query),
                        using=SPARSE,
                        filter=flt,
                        limit=self._candidates,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                limit=fetch,
                with_payload=True,
            )
        hits = [_to_hit(p) for p in response.points]
        if mode == "hybrid_rerank" and hits:
            scores = self._embedder.rerank(query, [f"{h.title}\n{h.text}" for h in hits])
            ranked = sorted(zip(hits, scores, strict=True), key=lambda pair: pair[1], reverse=True)
            hits = [replace(h, score=s) for h, s in ranked][:limit]
        return hits
