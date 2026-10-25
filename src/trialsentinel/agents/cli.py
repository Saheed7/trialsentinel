"""Missing Results agent command line.

Examples:
    uv run python -m trialsentinel.agents.cli run NCT03317028
    uv run python -m trialsentinel.agents.cli eval --limit 6 --judge --export data/eval/claims.csv
    uv run python -m trialsentinel.agents.cli score-labels data/eval/claims.csv
"""

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import select

from trialsentinel.agents.evidence import Case, build_evidence, load_case
from trialsentinel.agents.labels import (
    LABEL_COLUMN,
    interactive_label,
    load_rows,
    score,
    write_rows,
)
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


def _run_case(case: Case, retriever: Any, graph: Any, context_tokens: int) -> dict[str, Any]:
    hits = retriever.search(case.brief_title, limit=3, mode="hybrid", nct_id=case.nct_id)
    evidence = build_evidence(case, hits)
    by_id = {e.id: e.text for e in evidence}
    estimated_tokens = len(format_evidence(evidence)) // 4 + 400  # rough: 4 chars/token + prompt
    started = time.perf_counter()
    state = graph.invoke(
        {
            "nct_id": case.nct_id,
            "verdict": case.verdict,
            "evidence": evidence,
            "revisions": 0,
            "rejected": [],
            "audit": [],
            "rounds": [],
        }
    )
    audit_rows = [
        row
        | {
            "nct_id": case.nct_id,
            "cited_text": " | ".join(by_id.get(i, "") for i in row["evidence_ids"].split(",") if i),
            LABEL_COLUMN: "",
        }
        for row in state.get("audit", [])
    ]
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
        "audit_rows": audit_rows,
        "estimated_prompt_tokens": estimated_tokens,
        "context_warning": estimated_tokens > context_tokens,
        "seconds": round(time.perf_counter() - started, 1),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trialsentinel-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run the agent on one trial")
    run.add_argument("nct_id")
    ev = sub.add_parser("eval", help="Run on rule findings; optionally export claims to label")
    ev.add_argument("--limit", type=int, default=6)
    ev.add_argument("--export", type=Path, default=None, help="CSV of claims for human labelling")
    for p in (run, ev):
        p.add_argument("--judge", action="store_true", help="Add the LLM entailment judge")
        p.add_argument("--as-of", type=date.fromisoformat, default=DEFAULT_AS_OF)
    lb = sub.add_parser("label", help="Label exported claims interactively (blind)")
    lb.add_argument("path", type=Path)
    sc = sub.add_parser("score-labels", help="Score verifier layers against human labels")
    sc.add_argument("path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "score-labels":
        print(json.dumps(score(load_rows(args.path)), indent=2))
        return 0

    if args.command == "label":
        print(json.dumps(interactive_label(args.path), indent=2))
        return 0
    # Heavy imports only when a model is needed.
    from qdrant_client import QdrantClient

    from trialsentinel.llm.factory import get_chat_model
    from trialsentinel.llm.usage import UsageCallback
    from trialsentinel.retrieval.embeddings import Embedder
    from trialsentinel.retrieval.search import HybridRetriever

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
        single = {k: v for k, v in results[0].items() if k != "audit_rows"}
        print(json.dumps(single | {"usage": usage}, indent=2))
        return 0

    rows = [row for r in results for row in r["audit_rows"]]
    if args.export:
        write_rows(args.export, rows)
    first = [r["rounds"][0] for r in results]
    drafted = sum(f["claims"] for f in first)
    summary = {
        "cases": len(results),
        "first_draft_claims": drafted,
        # Verifier-reported, NOT a hallucination rate: score against human labels for that.
        "first_draft_rejection_rate": round(sum(f["rejected"] for f in first) / drafted, 3)
        if drafted
        else None,
        "claims_audited": len(rows),
        "deterministic_rejections": sum(r["deterministic"] != "pass" for r in rows),
        "judge_rejections": sum(r["judge"] == "unsupported" for r in rows),
        "published_claims": sum(len(r["claims"]) for r in results),
        "needs_review": sum(r["status"] == "needs_review" for r in results),
        "revisions": sum(r["revisions"] for r in results),
        "context_warnings": sum(r["context_warning"] for r in results),
        "mean_seconds_per_case": round(sum(r["seconds"] for r in results) / len(results), 1),
        "exported_to": str(args.export) if args.export else None,
    }
    print(json.dumps(summary | {"usage": usage}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
