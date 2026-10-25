"""Qdrant collection management and idempotent indexing (Qdrant is rebuildable, see ADR-0001)."""

from collections.abc import Sequence
from itertools import batched

from qdrant_client import QdrantClient, models

from trialsentinel.retrieval.chunking import chunk_id, chunk_text
from trialsentinel.retrieval.documents import SourceDocument
from trialsentinel.retrieval.embeddings import Embedder

DENSE = "dense"
SPARSE = "sparse"
KEYWORD_FIELDS = ("doc_type", "source_id", "nct_ids")
UPSERT_BATCH = 128


def ensure_collection(
    client: QdrantClient, name: str, dense_dim: int, *, recreate: bool = False
) -> None:
    if recreate and client.collection_exists(name):
        client.delete_collection(name)
    if client.collection_exists(name):
        return
    client.create_collection(
        name,
        vectors_config={
            DENSE: models.VectorParams(size=dense_dim, distance=models.Distance.COSINE)
        },
        sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    for field in KEYWORD_FIELDS:
        client.create_payload_index(
            name, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
        )


def build_points(
    docs: Sequence[SourceDocument], embedder: Embedder, *, max_chars: int, overlap_chars: int
) -> list[models.PointStruct]:
    records = [
        (doc, i, chunk)
        for doc in docs
        for i, chunk in enumerate(
            chunk_text(doc.text, max_chars=max_chars, overlap_chars=overlap_chars)
        )
    ]
    # The title is embedded with every chunk so each chunk carries its document's topic.
    texts = [f"{doc.title}\n{chunk}" for doc, _, chunk in records]
    dense = embedder.embed_dense(texts)
    sparse = embedder.embed_sparse(texts)
    return [
        models.PointStruct(
            id=chunk_id(doc.doc_type, doc.source_id, i),
            vector={DENSE: d, SPARSE: s},
            payload={
                "doc_type": doc.doc_type,
                "source_id": doc.source_id,
                "title": doc.title,
                "text": chunk,
                "chunk_index": i,
                "nct_ids": list(doc.nct_ids),
                "pub_date": doc.pub_date,
            },
        )
        for (doc, i, chunk), d, s in zip(records, dense, sparse, strict=True)
    ]


def upsert_points(client: QdrantClient, name: str, points: Sequence[models.PointStruct]) -> None:
    for batch in batched(points, UPSERT_BATCH):
        client.upsert(name, points=list(batch), wait=True)
