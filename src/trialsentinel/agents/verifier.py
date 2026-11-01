"""Claim-level grounding (verifier v3).

Layers, cheapest first:
1. Structural: the claim cites evidence IDs that exist.
2. Token grounding: dates, numbers and trial IDs must appear in the cited evidence
   (the audited trial's own ID is context, not a claim).
3. Typed rules for systematic errors found in the labelled baseline:
   B. a primary completion date presented as the completion date;
   C. a linked paper presented as the trial's publication or results report.
An optional LLM judge (missing_results.py) checks the remaining semantics.

v3 fixes two faults found on held-out set 1 (which is therefore a validation set now):
- trial IDs written with a space ("NCT 01169259") are normalised before matching;
- rule C no longer fires on negated wording ("has not been published in PubMed")."""

import re
from collections.abc import Mapping

from pydantic import BaseModel, Field

from trialsentinel.agents.evidence import Evidence

VERIFIER_VERSION = 3

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_NUMBER = re.compile(r"\b\d[\d,]*(?:\.\d+)?\b")
_IDENTIFIER = re.compile(r"\bNCT\d{8}\b")
_SPACED_IDENTIFIER = re.compile(r"\bNCT[\s-]+(\d{8})\b")
_COMPLETED_ON_DATE = re.compile(r"\bcompleted\b[^.;]*?\b(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_PUBLISHED_IN = re.compile(r"\bpublished in\b", re.IGNORECASE)
_TRIAL_PUBLISHED_IN = re.compile(r"\btrial\b[^.;]*\bpublished in\b", re.IGNORECASE)
_REPORTED_RESULTS = re.compile(
    r"\b(?:report|reports|reported|present|presents|presented)\s+the\s+results\s+of\b",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"(?:\b(?:not|never|no)\b|n't\b)", re.IGNORECASE)
_LINK_LINE = re.compile(r"PubMed (\d+) is linked to NCT\d{8} via ([a-z_, ]+);")

REASON_COMPLETION = "rule B: a primary completion date is presented as the completion date"
REASON_PUBLICATION = (
    "rule C: a linked paper is presented as the trial's publication or results report; "
    "only a registry_result link establishes that"
)


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
        description="true only if the evidence supports every part of the claim"
    )


def clean_ids(ids: list[str]) -> list[str]:
    """Tolerate '[E1]' and ' E1 ' as well as 'E1'."""
    return [i.strip().strip("[]").strip() for i in ids if i.strip()]


def normalise_identifiers(text: str) -> str:
    """'NCT 01169259' and 'NCT-01169259' -> 'NCT01169259'."""
    return _SPACED_IDENTIFIER.sub(r"NCT\1", text)


def grounding_tokens(text: str) -> list[str]:
    """Dates, numbers, and trial identifiers: things a claim must not invent."""
    text = normalise_identifiers(text)
    tokens = _DATE.findall(text) + _IDENTIFIER.findall(text)
    tokens += [n.replace(",", "") for n in _NUMBER.findall(text)]
    return list(dict.fromkeys(tokens))


def cited_text(claim: Claim, evidence: Mapping[str, Evidence]) -> str:
    return " ".join(evidence[i].text for i in clean_ids(claim.evidence_ids) if i in evidence)


def _negated_before(text: str, position: int, window: int = 30) -> bool:
    """True if a negation word appears shortly before `position` (same clause)."""
    before = re.split(r"[.;,]", text[max(0, position - window) : position])[-1]
    return bool(_NEGATION.search(before))


def _claims_publication(text: str) -> bool:
    """A positive (non-negated) claim that the trial was published or its results reported."""
    if _TRIAL_PUBLISHED_IN.search(text):
        for match in _PUBLISHED_IN.finditer(text):
            if not _negated_before(text, match.start()):
                return True
    for match in _REPORTED_RESULTS.finditer(text):
        if not _negated_before(text, match.start()):
            return True
    return False


def check_text(text: str, cited: str, *, subject_ids: frozenset[str] = frozenset()) -> str | None:
    """Content checks on a claim against the text it cites. None means it passes."""
    cited = normalise_identifiers(cited)
    flat = cited.replace(",", "")
    for token in grounding_tokens(text):
        if token not in subject_ids and token not in flat:
            return f"'{token}' does not appear in the cited evidence"

    completed = _COMPLETED_ON_DATE.search(text)
    if (
        completed
        and "primary completion" not in text.lower()
        and f"primary completion {completed.group(1)}" in cited
    ):
        return REASON_COMPLETION

    if _claims_publication(text):
        links = _LINK_LINE.findall(cited)
        if not links or any("registry_result" not in sources for _, sources in links):
            return REASON_PUBLICATION
    return None


def check_claim(
    claim: Claim,
    evidence: Mapping[str, Evidence],
    *,
    subject_ids: frozenset[str] = frozenset(),
) -> str | None:
    """Returns None if the claim passes, otherwise the rejection reason."""
    ids = clean_ids(claim.evidence_ids)
    if not ids:
        return "cites no evidence"
    unknown = [i for i in ids if i not in evidence]
    if unknown:
        return f"cites unknown evidence: {', '.join(unknown)}"
    return check_text(claim.text, cited_text(claim, evidence), subject_ids=subject_ids)
