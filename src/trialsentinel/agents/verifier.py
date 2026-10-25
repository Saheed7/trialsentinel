"""Claim-level grounding. Deterministic checks always run; an optional LLM judge checks
entailment. A claim survives only if every check passes.

The deterministic layer is a lower bound: it catches invented dates, numbers, and
identifiers, but not a false claim that contains none (that is the judge's job)."""

import re
from collections.abc import Mapping

from pydantic import BaseModel, Field

from trialsentinel.agents.evidence import Evidence

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
_IDENTIFIER = re.compile(r"\bNCT\d{8}\b")


class Claim(BaseModel):
    text: str = Field(description="One short factual sentence.")
    evidence_ids: list[str] = Field(
        default_factory=list,
        description="IDs of the evidence items that support this sentence, for example ['E1'].",
    )


class Draft(BaseModel):
    claims: list[Claim] = Field(default_factory=list, description="Two to five claims.")


class Judgement(BaseModel):
    supported: bool = Field(
        description="true only if the evidence alone fully supports every part of the claim"
    )


def clean_ids(ids: list[str]) -> list[str]:
    """Tolerate '[E1]' and ' E1 ' as well as 'E1'."""
    return [i.strip().strip("[]").strip() for i in ids if i.strip()]


def grounding_tokens(text: str) -> list[str]:
    """Dates, numbers, and trial identifiers: things a claim must not invent."""
    tokens = _DATE.findall(text) + _IDENTIFIER.findall(text)
    tokens += [n.replace(",", "") for n in _NUMBER.findall(text)]
    return list(dict.fromkeys(tokens))


def cited_text(claim: Claim, evidence: Mapping[str, Evidence]) -> str:
    return " ".join(evidence[i].text for i in clean_ids(claim.evidence_ids) if i in evidence)


def check_claim(claim: Claim, evidence: Mapping[str, Evidence]) -> str | None:
    """Returns None if the claim passes, otherwise the rejection reason."""
    ids = clean_ids(claim.evidence_ids)
    if not ids:
        return "cites no evidence"
    unknown = [i for i in ids if i not in evidence]
    if unknown:
        return f"cites unknown evidence: {', '.join(unknown)}"
    cited = cited_text(claim, evidence).replace(",", "")
    for token in grounding_tokens(claim.text):
        if token not in cited:
            return f"'{token}' does not appear in the cited evidence"
    return None
