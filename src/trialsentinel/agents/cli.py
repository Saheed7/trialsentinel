"""Missing Results agent command line.

Examples:
    uv run python -m trialsentinel.agents.cli run NCT03317028
    uv run python -m trialsentinel.agents.cli run NCT03317028 --judge
    uv run python -m trialsentinel.agents.cli eval --limit 6
"""

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from datetime import date
from typing import Any

from qdrant_client import QdrantClient
from sqlalchemy import select

from trialsentinel.agents.evidence import Case, build_evidence, load_case
from trialsentinel.agents.missing_results import (
    build_graph,
    format_evidence,
    make_llm_drafter,
    make_llm_judge,
)
from trialsentinel.core.config import get_settings
from trialsentinel.core.logging import configure_logging
from trialsentinel.db.models import FdaaaAssessment
from trialsentinel.db.session import get_engine, get_sessionmaker
from trialsentinel.llm.factory import get_chat_model
from trialsentinel.llm.usage import UsageCallback
from trialsentinel.retrieval.embeddings import Embedder
from trialsentinel.retrieval.search import HybridRetriever

DEFAULT_AS_OF = date(2026, 10, 5)  # fixed for reproducible runs


async def _load_cases(nct_ids: list[str], *, as_of: date, limit: int) -> list[Case]:
    try:
        async with get_sessionmaker()() as session:
            if not nct_ids:
                stmt = (
                    select(FdaaaAssessment.nct_id)
                    .where(
                        FdaaaAssessment.as_of_date == as_of, FdaaaAssessment.finding.is_not(None)
                    )
                    .order_by(FdaaaAssessment.nct_id)
                    .limit(limit)
                )
                nct_ids = list((await session.scalars(stmt)).all())
            cases = []
            for nct_id in nct_ids:
                if case := await load_case(session, nct_id, as_of=as_of):
                    cases.append(case)
            return cases
    finally:
        await get_engine().dispose()


def _run_case(
    case: Case, retriever: HybridRetriever, graph: Any, context_tokens: int
) -> dict[str, Any]:
    hits = retriever.search(case.brief_title, limit=3, mode="hybrid", nct_id=case.nct_id)
    evidence = build_evidence(case, hits)
    estimated_tokens = len(format_evidence(evidence)) // 4 + 400  # rough: 4 chars/token + prompt
    started = time.perf_counter()
    state = graph.invoke(
        {
            "nct_id": case.nct_id,
            "verdict": case.verdict,
            "evidence": evidence,
            "revisions": 0,
            "rejected": [],
            "rounds": [],
        }
    )
    return {
        "nct_id": case.nct_id,
        "verdict": case.verdict,
        "status": state["status"],
        "claims": [
            {"text": c.text, "evidence_ids": c.evidence_ids} for c in state.get("verified", [])
        ],
        "rejected": state.get("rejected", []),
        "rounds": state.get("rounds", []),
        "revisions": state.get("revisions", 0),
        "evidence": [{"id": e.id, "kind": e.kind, "text": e.text} for e in evidence],
        "estimated_prompt_tokens": estimated_tokens,
        "context_warning": estimated_tokens > context_tokens,
        "seconds": round(time.perf_counter() - started, 1),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trialsentinel-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run the agent on one trial")
    run.add_argument("nct_id")
    ev = sub.add_parser("eval", help="Run on rule findings and report grounding metrics")
    ev.add_argument("--limit", type=int, default=6)
    for p in (run, ev):
        p.add_argument("--judge", action="store_true", help="Add the LLM entailment judge")
        p.add_argument("--as-of", type=date.fromisoformat, default=DEFAULT_AS_OF)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    s = get_settings()
    configure_logging(s.log_level, json_logs=s.env != "local")

    nct_ids = [args.nct_id] if args.command == "run" else []
    limit = 1 if args.command == "run" else args.limit
    cases = asyncio.run(_load_cases(nct_ids, as_of=args.as_of, limit=limit))
    if not cases:
        print(json.dumps({"error": "no assessed trial found; run the rule engine first"}))
        return 1

    tracker = UsageCallback()
    model = get_chat_model(s)
    graph = build_graph(
        make_llm_drafter(model, s.llm_structured_method, [tracker]),
        make_llm_judge(model, s.llm_structured_method, [tracker]) if args.judge else None,
    )
    embedder = Embedder(
        dense_model=s.embedding_model,
        sparse_model=s.sparse_model,
        rerank_model=s.rerank_model,
        cache_dir=s.model_cache_dir,
    )
    retriever = HybridRetriever(QdrantClient(url=s.qdrant_url), s.qdrant_collection, embedder)

    results = [_run_case(c, retriever, graph, s.llm_context_tokens) for c in cases]
    usage = {
        "model": s.llm_model,
        "judge": args.judge,
        "llm_calls": tracker.totals.calls,
        "input_tokens": tracker.totals.input_tokens,
        "output_tokens": tracker.totals.output_tokens,
    }

    if args.command == "run":
        print(json.dumps(results[0] | {"usage": usage}, indent=2))
        return 0

    first = [r["rounds"][0] for r in results]
    last = [r["rounds"][-1] for r in results]
    drafted, rejected_first = sum(f["claims"] for f in first), sum(f["rejected"] for f in first)
    final_claims, rejected_last = sum(x["claims"] for x in last), sum(x["rejected"] for x in last)
    summary = {
        "cases": len(results),
        "first_draft_claims": drafted,
        "first_draft_unsupported_rate": round(rejected_first / drafted, 3) if drafted else None,
        "after_revision_unsupported_rate": round(rejected_last / final_claims, 3)
        if final_claims
        else None,
        "published_claims": sum(len(r["claims"]) for r in results),
        "needs_review": sum(r["status"] == "needs_review" for r in results),
        "revisions": sum(r["revisions"] for r in results),
        "context_warnings": sum(r["context_warning"] for r in results),
        "mean_seconds_per_case": round(sum(r["seconds"] for r in results) / len(results), 1),
        "rejection_reasons": [r for res in results for r in res["rejected"]],
    }
    print(json.dumps(summary | {"usage": usage}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
