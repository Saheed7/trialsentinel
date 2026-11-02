"""Running the Missing Results agent on one trial, shared by the CLI and the API.

Database access stays with the caller (the case is loaded first), so this module is
synchronous and DB-free: it needs only the retriever and the language models."""

import time
from dataclasses import dataclass
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

DEFAULT_AS_OF = date(2026, 10, 5)  # fixed for reproducible runs


@dataclass(frozen=True)
class AuditOptions:
    prompt_version: int = 2
    judge: bool = False
    judge_prompt_version: int = 2


def chat_model(settings: Settings, name: str | None) -> Any:
    from trialsentinel.llm.factory import get_chat_model

    return get_chat_model(settings.model_copy(update={"llm_model": name}) if name else settings)


def run_case(case: Case, retriever: Any, graph: Any, context_tokens: int) -> dict[str, Any]:
    """Runs the agent graph on one loaded case: claims, rejections and the audit trail."""
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


class AgentRunner:
    """Builds the retriever and agent graphs once, lazily, and reuses them across audits.

    The first audit pays the model-loading cost; later audits reuse everything."""

    def __init__(self, settings: Settings, *, judge_model: str | None = None) -> None:
        self._settings = settings
        self._judge_model = judge_model
        self._retriever: Any = None
        self._graphs: dict[AuditOptions, Any] = {}
        self._lock = Lock()

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
                chat_model(s, None), method, [], prompt_version=options.prompt_version
            )
            judge = (
                make_llm_judge(
                    chat_model(s, self._judge_model),
                    method,
                    [],
                    prompt_version=options.judge_prompt_version,
                )
                if options.judge
                else None
            )
            self._graphs[options] = build_graph(drafter, judge)
        return self._graphs[options]

    def run(self, case: Case, options: AuditOptions) -> dict[str, Any]:
        with self._lock:  # one audit at a time: one local GPU, one loaded model
            result = run_case(
                case,
                self._get_retriever(),
                self._get_graph(options),
                self._settings.llm_context_tokens,
            )
        return result | {
            "verifier_version": VERIFIER_VERSION,
            "prompt_version": options.prompt_version,
            "judge": options.judge,
        }
