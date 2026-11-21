"""Running the Missing Results agent on one trial, shared by the CLI and the API.

Database access stays with the caller (the case is loaded first), so this module is
synchronous and DB-free: it needs only the retriever and the language models."""

import time
from dataclasses import dataclass, replace
from datetime import date
from threading import Lock
from typing import Any

from trialsentinel.agents.evidence import Case, build_evidence
from trialsentinel.agents.labels import LABEL_COLUMN
from trialsentinel.agents.missing_results import (
    build_graph,
    format_evidence,
    make_llm_drafter,
    make_llm_judge,
)
from trialsentinel.agents.verifier import VERIFIER_VERSION
from trialsentinel.core.config import Settings
from trialsentinel.llm.usage import UsageCallback

DEFAULT_AS_OF = date(2026, 10, 5)  # fixed for reproducible runs


@dataclass(frozen=True)
class AuditOptions:
    prompt_version: int = 2
    judge: bool = False
    judge_prompt_version: int = 2


def chat_model(settings: Settings, name: str | None) -> Any:
    from trialsentinel.llm.factory import get_chat_model

    return get_chat_model(settings.model_copy(update={"llm_model": name}) if name else settings)


def decision_category(reason: str) -> str:
    """Maps a deterministic verifier outcome to a small, fixed set of metric labels."""
    if reason == "pass":
        return "pass"
    if reason.startswith("rule B"):
        return "rule_b"
    if reason.startswith("rule C"):
        return "rule_c"
    if reason.startswith("cites "):
        return "structure"
    if "does not appear in the cited evidence" in reason:
        return "token"
    return "other"


def summarize_decisions(audit_rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Counts every claim's decision per verifier layer, across all revision rounds."""
    deterministic: dict[str, int] = {}
    judge: dict[str, int] = {}
    for row in audit_rows:
        category = decision_category(str(row.get("deterministic", "pass")))
        deterministic[category] = deterministic.get(category, 0) + 1
        verdict = str(row.get("judge", "n/a"))
        if verdict != "n/a":
            judge[verdict] = judge.get(verdict, 0) + 1
    return {"deterministic": deterministic, "judge": judge}


def run_case(case: Case, retriever: Any, graph: Any, context_tokens: int) -> dict[str, Any]:
    """Runs the agent graph on one loaded case: claims, rejections and the audit trail."""
    retrieval_started = time.perf_counter()
    hits = retriever.search(case.brief_title, limit=3, mode="hybrid", nct_id=case.nct_id)
    retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 1)
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
        "retrieval_ms": retrieval_ms,
        "seconds": round(time.perf_counter() - started, 1),
    }


class AgentRunner:
    """Builds the retriever and agent graphs once, lazily, and reuses them across audits.

    The first audit pays the model-loading cost; later audits reuse everything."""

    def __init__(self, settings: Settings, *, judge_model: str | None = None) -> None:
        self._settings = settings
        self._judge_model = judge_model
        self._retriever: Any = None
        self._graphs: dict[AuditOptions, Any] = {}
        self._lock = Lock()
        self._usage = UsageCallback()  # cumulative; per-audit usage is the difference

    def _get_retriever(self) -> Any:
        if self._retriever is None:
            from qdrant_client import QdrantClient

            from trialsentinel.retrieval.embeddings import Embedder
            from trialsentinel.retrieval.search import HybridRetriever

            s = self._settings
            embedder = Embedder(
                dense_model=s.embedding_model,
                sparse_model=s.sparse_model,
                rerank_model=s.rerank_model,
                cache_dir=s.model_cache_dir,
            )
            self._retriever = HybridRetriever(
                QdrantClient(url=s.qdrant_url), s.qdrant_collection, embedder
            )
        return self._retriever

    def _get_graph(self, options: AuditOptions) -> Any:
        if options not in self._graphs:
            s = self._settings
            method = s.llm_structured_method
            drafter = make_llm_drafter(
                chat_model(s, None), method, [self._usage], prompt_version=options.prompt_version
            )
            judge = (
                make_llm_judge(
                    chat_model(s, self._judge_model),
                    method,
                    [self._usage],
                    prompt_version=options.judge_prompt_version,
                )
                if options.judge
                else None
            )
            self._graphs[options] = build_graph(drafter, judge)
        return self._graphs[options]

    def run(self, case: Case, options: AuditOptions) -> dict[str, Any]:
        with self._lock:  # one audit at a time: one local GPU, one loaded model
            retriever, graph = self._get_retriever(), self._get_graph(options)
            before = replace(self._usage.totals, latencies=[])
            result = run_case(case, retriever, graph, self._settings.llm_context_tokens)
            after = self._usage.totals
            trace = {
                "retrieval_ms": result["retrieval_ms"],
                "agent_s": result["seconds"],
                "llm_calls": after.calls - before.calls,
                "llm_latency_s": round(after.latency_s - before.latency_s, 2),
                "input_tokens": after.input_tokens - before.input_tokens,
                "output_tokens": after.output_tokens - before.output_tokens,
                "estimated_prompt_tokens": result["estimated_prompt_tokens"],
                "context_warning": result["context_warning"],
                "decisions": summarize_decisions(result["audit_rows"]),
            }
        return result | {
            "verifier_version": VERIFIER_VERSION,
            "prompt_version": options.prompt_version,
            "judge": options.judge,
            "trace": trace,
        }
