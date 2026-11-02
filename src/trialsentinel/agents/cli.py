"""Missing Results agent command line.

Examples:
    uv run python -m trialsentinel.agents.cli run NCT03317028
    uv run python -m trialsentinel.agents.cli eval --offset 6 --limit 5 --export data/eval/h.csv
    uv run python -m trialsentinel.agents.cli recheck data/eval/claims.csv
    uv run python -m trialsentinel.agents.cli rejudge data/eval/claims.csv --model qwen2.5:7b
    uv run python -m trialsentinel.agents.cli findings
    uv run python -m trialsentinel.agents.cli calibrate
    uv run python -m trialsentinel.agents.cli label data/eval/claims.csv
    uv run python -m trialsentinel.agents.cli score-labels data/eval/claims.csv
    uv run python -m trialsentinel.agents.cli agreement data/eval/a.csv data/eval/b.csv
"""

import argparse
import asyncio
import csv
import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from sqlalchemy import select

from trialsentinel.agents.evidence import Case, load_case
from trialsentinel.agents.labels import (
    agreement,
    calibrate,
    interactive_label,
    load_rows,
    score,
    write_rows,
)
from trialsentinel.agents.missing_results import (
    JUDGE_PROMPTS,
    PROMPTS,
    build_graph,
    make_llm_drafter,
    make_llm_judge,
)
from trialsentinel.agents.offline import recheck, rejudge
from trialsentinel.agents.service import DEFAULT_AS_OF
from trialsentinel.agents.service import chat_model as _model
from trialsentinel.agents.service import run_case as _run_case
from trialsentinel.agents.verifier import VERIFIER_VERSION
from trialsentinel.core.config import get_settings
from trialsentinel.core.logging import configure_logging
from trialsentinel.db.models import FdaaaAssessment
from trialsentinel.db.session import get_engine, get_sessionmaker


def _ids_from_csvs(paths: Sequence[Path]) -> set[str]:
    """Trial IDs already used in earlier evaluation files (development, validation)."""
    used: set[str] = set()
    for path in paths:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            used |= {row["nct_id"] for row in csv.DictReader(handle) if row.get("nct_id")}
    return used


async def _load_cases(
    nct_ids: list[str],
    *,
    as_of: date,
    limit: int,
    offset: int = 0,
    exclude: frozenset[str] = frozenset(),
) -> list[Case]:
    try:
        async with get_sessionmaker()() as session:
            if not nct_ids:
                stmt = (
                    select(FdaaaAssessment.nct_id)
                    .where(
                        FdaaaAssessment.as_of_date == as_of, FdaaaAssessment.finding.is_not(None)
                    )
                    .where(FdaaaAssessment.nct_id.not_in(exclude))
                    .distinct()
                    .order_by(FdaaaAssessment.nct_id)
                    .offset(offset)
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


async def _list_findings(as_of: date) -> list[str]:
    """Rule findings in the same order `eval --offset` uses, so offsets can be planned."""
    try:
        async with get_sessionmaker()() as session:
            stmt = (
                select(FdaaaAssessment.nct_id)
                .where(FdaaaAssessment.as_of_date == as_of, FdaaaAssessment.finding.is_not(None))
                .distinct()
                .order_by(FdaaaAssessment.nct_id)
            )
            return list((await session.scalars(stmt)).all())
    finally:
        await get_engine().dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trialsentinel-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run the agent on one trial")
    run.add_argument("nct_id")
    ev = sub.add_parser("eval", help="Run on rule findings; optionally export claims to label")
    ev.add_argument("--limit", type=int, default=6)
    ev.add_argument("--offset", type=int, default=0, help="Skip the first N findings")
    ev.add_argument(
        "--exclude",
        type=Path,
        action="append",
        default=[],
        help="CSV whose trials must not be used (repeatable), e.g. earlier eval files",
    )
    ev.add_argument("--export", type=Path, default=None, help="CSV of claims for human labelling")
    for p in (run, ev):
        p.add_argument("--judge", action="store_true", help="Add the LLM judge inside the loop")
        p.add_argument("--judge-model", default=None, help="Judge model (default: drafting model)")
        p.add_argument("--judge-prompt-version", type=int, choices=sorted(JUDGE_PROMPTS), default=1)
        p.add_argument("--prompt-version", type=int, choices=sorted(PROMPTS), default=2)
        p.add_argument("--as-of", type=date.fromisoformat, default=DEFAULT_AS_OF)
    rc = sub.add_parser("recheck", help="Re-run current deterministic checks on frozen claims")
    rc.add_argument("path", type=Path)
    rj = sub.add_parser("rejudge", help="Add one judge model's verdicts to frozen claims")
    rj.add_argument("path", type=Path)
    rj.add_argument("--model", required=True)
    rj.add_argument("--prompt-version", type=int, choices=sorted(JUDGE_PROMPTS), default=1)
    lb = sub.add_parser("label", help="Label exported claims interactively (blind)")
    lb.add_argument("path", type=Path)
    lb.add_argument(
        "--skip-calibration", action="store_true", help="Only if calibration was passed today"
    )
    lb.add_argument("--calibration-set", choices=["a", "b"], default="a")
    cb = sub.add_parser("calibrate", help="Practice items with known answers, before labelling")
    cb.add_argument("--set", dest="calibration_set", choices=["a", "b"], default="a")
    fd = sub.add_parser("findings", help="List rule findings and their eval offsets")
    fd.add_argument("--as-of", type=date.fromisoformat, default=DEFAULT_AS_OF)
    fd.add_argument("--exclude", type=Path, action="append", default=[])
    ag = sub.add_parser("agreement", help="Cohen's kappa between two annotators' files")
    ag.add_argument("path_a", type=Path)
    ag.add_argument("path_b", type=Path)
    sc = sub.add_parser("score-labels", help="Score verifier layers against human labels")
    sc.add_argument("path", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "score-labels":
        print(json.dumps(score(load_rows(args.path)), indent=2))
        return 0
    if args.command == "agreement":
        print(json.dumps(agreement(load_rows(args.path_a), load_rows(args.path_b)), indent=2))
        return 0
    if args.command == "calibrate":
        print(json.dumps(calibrate(args.calibration_set), indent=2))
        return 0
    if args.command == "label":
        if not args.skip_calibration and not calibrate(args.calibration_set)["passed"]:
            print(json.dumps({"error": "calibration not passed; labelling not started"}))
            return 1
        print(json.dumps(interactive_label(args.path), indent=2))
        return 0
    if args.command == "findings":
        ids = asyncio.run(_list_findings(args.as_of))
        used = _ids_from_csvs(args.exclude)
        unused = [nct for nct in ids if nct not in used]
        print(
            json.dumps(
                {
                    "as_of": args.as_of.isoformat(),
                    "findings": len(ids),
                    "already_used": sorted(used & set(ids)),
                    "unused": unused,
                    "unused_count": len(unused),
                },
                indent=2,
            )
        )
        return 0
    if args.command == "recheck":
        rows = load_rows(args.path)
        changed = recheck(rows)
        write_rows(args.path, rows)
        print(json.dumps({"verifier_version": VERIFIER_VERSION, "changed": changed}, indent=2))
        return 0

    s = get_settings()
    configure_logging(s.log_level, json_logs=s.env != "local")

    from trialsentinel.llm.usage import UsageCallback

    tracker = UsageCallback()
    if args.command == "rejudge":
        rows = load_rows(args.path)
        judge = make_llm_judge(
            _model(s, args.model),
            s.llm_structured_method,
            [tracker],
            prompt_version=args.prompt_version,
        )
        column = f"judge_{args.model}_p{args.prompt_version}"
        rejected = rejudge(rows, judge, column)
        write_rows(args.path, rows)
        print(json.dumps({"column": column, "claims": len(rows), "rejected": rejected}, indent=2))
        return 0

    # Heavy imports only when the full agent runs.
    from qdrant_client import QdrantClient

    from trialsentinel.retrieval.embeddings import Embedder
    from trialsentinel.retrieval.search import HybridRetriever

    nct_ids = [args.nct_id] if args.command == "run" else []
    limit = 1 if args.command == "run" else args.limit
    offset = 0 if args.command == "run" else args.offset
    exclude = frozenset(_ids_from_csvs(args.exclude)) if args.command == "eval" else frozenset()
    cases = asyncio.run(
        _load_cases(nct_ids, as_of=args.as_of, limit=limit, offset=offset, exclude=exclude)
    )
    if not cases:
        print(json.dumps({"error": "no assessed trial found; run the rule engine first"}))
        return 1

    graph = build_graph(
        make_llm_drafter(
            _model(s, None), s.llm_structured_method, [tracker], prompt_version=args.prompt_version
        ),
        make_llm_judge(
            _model(s, args.judge_model),
            s.llm_structured_method,
            [tracker],
            prompt_version=args.judge_prompt_version,
        )
        if args.judge
        else None,
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
        "prompt_version": args.prompt_version,
        "verifier_version": VERIFIER_VERSION,
        "judge": args.judge,
        "judge_model": args.judge_model or (s.llm_model if args.judge else None),
        "judge_prompt_version": args.judge_prompt_version if args.judge else None,
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
        "cases": [r["nct_id"] for r in results],
        "first_draft_claims": drafted,
        # Verifier-reported, NOT a hallucination rate: score against human labels for that.
        "first_draft_rejection_rate": round(sum(f["rejected"] for f in first) / drafted, 3)
        if drafted
        else None,
        "claims_audited": len(rows),
        "deterministic_rejections": sum(r["deterministic"] != "pass" for r in rows),
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
