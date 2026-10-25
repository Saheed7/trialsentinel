"""Missing Results agent: explains a rule verdict, then verifies every claim.

The verdict comes from the deterministic rule engine and is never decided by the LLM.
Graph: draft -> verify -> (revise -> draft, at most once) -> finalize.
Every claim's deterministic and judge decisions are recorded in `audit` for scoring
against human labels."""

from collections.abc import Callable, Sequence
from typing import Any, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from trialsentinel.agents.evidence import Evidence
from trialsentinel.agents.verifier import (
    Claim,
    Draft,
    Judgement,
    check_claim,
    cited_text,
    clean_ids,
)

MAX_REVISIONS = 1

SYSTEM_PROMPT = (
    "You explain clinical-trial transparency audit findings. Use only the numbered evidence. "
    "The rule verdict is authoritative: explain it, never contradict or re-decide it. "
    "Write two to five short factual claims about reporting status, deadlines, and "
    "publications. Each claim must list the IDs of the evidence items that support it. "
    "Copy every number, date, and identifier exactly as it appears in the cited evidence; "
    "never compute, round, or estimate new numbers."
)
JUDGE_PROMPT = (
    "You check whether a claim is fully supported by evidence. Answer supported=false if "
    "any part of the claim is missing from, or contradicts, the evidence."
)

Drafter = Callable[[str, Sequence[Evidence], str | None], Draft]
Judge = Callable[[Claim, str], bool]


class AgentState(TypedDict, total=False):
    nct_id: str
    verdict: str
    evidence: list[Evidence]
    claims: list[Claim]
    verified: list[Claim]
    rejected: list[dict[str, str]]
    audit: list[dict[str, str]]
    rounds: list[dict[str, int]]
    feedback: str | None
    revisions: int
    status: str


def format_evidence(evidence: Sequence[Evidence]) -> str:
    return "\n".join(f"[{e.id}] ({e.kind}) {e.text}" for e in evidence)


def build_graph(drafter: Drafter, judge: Judge | None = None) -> Any:
    def draft(state: AgentState) -> AgentState:
        result = drafter(state["verdict"], state["evidence"], state.get("feedback"))
        return {"claims": list(result.claims)}

    def verify(state: AgentState) -> AgentState:
        by_id = {e.id: e for e in state["evidence"]}
        subject = frozenset({state["nct_id"]})
        round_no = str(state.get("revisions", 0))
        verified: list[Claim] = []
        rejected = list(state.get("rejected", []))
        audit = list(state.get("audit", []))
        newly_rejected = 0
        for claim in state.get("claims", []):
            deterministic = check_claim(claim, by_id, subject_ids=subject)
            judged = "n/a"
            if judge is not None:
                judged = "supported" if judge(claim, cited_text(claim, by_id)) else "unsupported"
            reason = deterministic or (
                "judge: not supported by the cited evidence" if judged == "unsupported" else None
            )
            audit.append(
                {
                    "round": round_no,
                    "claim": claim.text,
                    "evidence_ids": ",".join(clean_ids(claim.evidence_ids)),
                    "deterministic": deterministic or "pass",
                    "judge": judged,
                    "decision": "rejected" if reason else "accepted",
                }
            )
            if reason:
                rejected.append({"claim": claim.text, "reason": reason, "round": round_no})
                newly_rejected += 1
            else:
                verified.append(claim)
        rounds = [
            *state.get("rounds", []),
            {"claims": len(state.get("claims", [])), "rejected": newly_rejected},
        ]
        return {"verified": verified, "rejected": rejected, "audit": audit, "rounds": rounds}

    def route(state: AgentState) -> str:
        last = state["rounds"][-1]
        needs_work = last["rejected"] > 0 or not state.get("verified")
        return "revise" if needs_work and state.get("revisions", 0) < MAX_REVISIONS else "finalize"

    def revise(state: AgentState) -> AgentState:
        round_no = str(state.get("revisions", 0))
        problems = [
            f'- "{r["claim"]}": {r["reason"]}'
            for r in state.get("rejected", [])
            if r["round"] == round_no
        ]
        feedback = (
            "Your previous claims had these problems:\n" + "\n".join(problems)
            if problems
            else "You produced no verifiable claims."
        )
        feedback += "\nRewrite all claims. Cite evidence IDs and copy numbers and dates exactly."
        return {"feedback": feedback, "revisions": state.get("revisions", 0) + 1}

    def finalize(state: AgentState) -> AgentState:
        return {"status": "verified" if state.get("verified") else "needs_review"}

    graph = StateGraph(AgentState)
    graph.add_node("draft", draft)
    graph.add_node("verify", verify)
    graph.add_node("revise", revise)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "draft")
    graph.add_edge("draft", "verify")
    graph.add_conditional_edges("verify", route, {"revise": "revise", "finalize": "finalize"})
    graph.add_edge("revise", "draft")
    graph.add_edge("finalize", END)
    return graph.compile()


def make_llm_drafter(model: BaseChatModel, method: str, callbacks: list[Any]) -> Drafter:
    structured = model.with_structured_output(Draft, method=method)

    def drafter(verdict: str, evidence: Sequence[Evidence], feedback: str | None) -> Draft:
        human = (
            f"Rule verdict (authoritative, do not change it): {verdict}\n\n"
            f"Evidence:\n{format_evidence(evidence)}"
        )
        if feedback:
            human += f"\n\n{feedback}"
        out = structured.invoke(
            [("system", SYSTEM_PROMPT), ("human", human)], config={"callbacks": callbacks}
        )
        return out if isinstance(out, Draft) else Draft()

    return drafter


def make_llm_judge(model: BaseChatModel, method: str, callbacks: list[Any]) -> Judge:
    structured = model.with_structured_output(Judgement, method=method)

    def judge(claim: Claim, evidence_text: str) -> bool:
        if not evidence_text:
            return False
        out = structured.invoke(
            [
                ("system", JUDGE_PROMPT),
                ("human", f"Evidence:\n{evidence_text}\n\nClaim: {claim.text}"),
            ],
            config={"callbacks": callbacks},
        )
        return bool(isinstance(out, Judgement) and out.supported)

    return judge
