"""Retrieval command line.

Examples:
    uv run python -m trialsentinel.retrieval.cli index
    uv run python -m trialsentinel.retrieval.cli search "HbA1c reduction with SGLT2 inhibitors"
    uv run python -m trialsentinel.retrieval.cli eval
"""

import argparse
import asyncio
import json
import time
from collections import Counter
from collections.abc import Sequence

from qdrant_client import QdrantClient

from trialsentinel.core.config import Settings, get_settings
from trialsentinel.core.logging import configure_logging
from trialsentinel.db.session import get_engine, get_sessionmaker
from trialsentinel.retrieval.documents import SourceDocument, load_documents
from trialsentinel.retrieval.embeddings import Embedder
from trialsentinel.retrieval.evaluate import KnownItem, evaluate, load_known_items
from trialsentinel.retrieval.index import build_points, ensure_collection, upsert_points
from trialsentinel.retrieval.search import MODES, HybridRetriever


def _embedder(s: Settings) -> Embedder:
    return Embedder(
        dense_model=s.embedding_model,
        sparse_model=s.sparse_model,
        rerank_model=s.rerank_model,
        cache_dir=s.model_cache_dir,
    )


async def _load_documents() -> list[SourceDocument]:
    try:
        async with get_sessionmaker()() as session:
            return await load_documents(session)
    finally:
        await get_engine().dispose()


async def _load_items() -> list[KnownItem]:
    try:
        async with get_sessionmaker()() as session:
            return await load_known_items(session)
    finally:
        await get_engine().dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trialsentinel-retrieval")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index", help="Rebuild the Qdrant index from PostgreSQL")
    se = sub.add_parser("search", help="Query the index")
    se.add_argument("query")
    se.add_argument("--mode", choices=MODES, default="hybrid")
    se.add_argument("--doc-type", choices=["trial", "publication"], default=None)
    se.add_argument("--nct", default=None, help="Restrict to chunks linked to this NCT ID")
    se.add_argument("--limit", type=int, default=5)
    ev = sub.add_parser("eval", help="Known-item retrieval evaluation across modes")
    ev.add_argument("--k", type=int, default=5)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    s = get_settings()
    configure_logging(s.log_level, json_logs=s.env != "local")
    client = QdrantClient(url=s.qdrant_url)
    embedder = _embedder(s)

    if args.command == "index":
        started = time.perf_counter()
        docs = asyncio.run(_load_documents())
        points = build_points(
            docs, embedder, max_chars=s.chunk_max_chars, overlap_chars=s.chunk_overlap_chars
        )
        ensure_collection(client, s.qdrant_collection, embedder.dense_dim, recreate=True)
        upsert_points(client, s.qdrant_collection, points)
        report: object = {
            "documents": dict(Counter(d.doc_type for d in docs)),
            "chunks": len(points),
            "dense_dim": embedder.dense_dim,
            "seconds": round(time.perf_counter() - started, 1),
        }
    elif args.command == "search":
        retriever = HybridRetriever(client, s.qdrant_collection, embedder)
        hits = retriever.search(
            args.query, limit=args.limit, mode=args.mode, doc_type=args.doc_type, nct_id=args.nct
        )
        report = [
            {
                "score": round(h.score, 4),
                "doc_type": h.doc_type,
                "source_id": h.source_id,
                "title": h.title,
                "snippet": h.text[:200],
            }
            for h in hits
        ]
    else:
        items = asyncio.run(_load_items())
        retriever = HybridRetriever(client, s.qdrant_collection, embedder)
        report = evaluate(retriever, items, MODES, k=args.k)

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
