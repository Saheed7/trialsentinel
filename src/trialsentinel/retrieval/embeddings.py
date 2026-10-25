"""Local, free embedding and reranking models (fastembed / ONNX, CPU). Loaded lazily."""

from collections.abc import Iterable, Sequence
from functools import cached_property
from pathlib import Path

from fastembed import SparseTextEmbedding, TextEmbedding
from fastembed.rerank.cross_encoder import TextCrossEncoder
from qdrant_client import models


class Embedder:
    def __init__(
        self, *, dense_model: str, sparse_model: str, rerank_model: str, cache_dir: Path
    ) -> None:
        self._dense_name = dense_model
        self._sparse_name = sparse_model
        self._rerank_name = rerank_model
        self._cache_dir = str(cache_dir)

    @cached_property
    def dense(self) -> TextEmbedding:
        return TextEmbedding(model_name=self._dense_name, cache_dir=self._cache_dir)

    @cached_property
    def sparse(self) -> SparseTextEmbedding:
        return SparseTextEmbedding(model_name=self._sparse_name, cache_dir=self._cache_dir)

    @cached_property
    def reranker(self) -> TextCrossEncoder:
        return TextCrossEncoder(model_name=self._rerank_name, cache_dir=self._cache_dir)

    @cached_property
    def dense_dim(self) -> int:
        return len(next(iter(self.dense.embed(["dimension probe"]))))

    def embed_dense(self, texts: Iterable[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.dense.embed(list(texts))]

    def embed_sparse(self, texts: Iterable[str]) -> list[models.SparseVector]:
        return [
            models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())
            for e in self.sparse.embed(list(texts))
        ]

    def query_dense(self, query: str) -> list[float]:
        return next(iter(self.dense.query_embed(query))).tolist()

    def query_sparse(self, query: str) -> models.SparseVector:
        e = next(iter(self.sparse.query_embed(query)))
        return models.SparseVector(indices=e.indices.tolist(), values=e.values.tolist())

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        return [float(score) for score in self.reranker.rerank(query, list(documents))]
