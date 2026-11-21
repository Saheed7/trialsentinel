"""Provenance defences against injected evidence (red-team defended arm, verifier "v4").

Excerpts are free text written by sponsors and authors, so an attacker can plant text in them.
Grounding a claim in its cited text then proves nothing: the planted text is the citation.
These defences separate trusted evidence (built by TrialSentinel from structured database
fields: rule, registry, publication) from untrusted excerpts:

1. `sanitise_excerpt` neutralises text that imitates TrialSentinel's own evidence format
   (evidence IDs, kind markers, publication-link lines) and labels the excerpt as untrusted.
2. `check_claim_v4` adds rule D: a claim about reporting status, deadlines, results, exemption
   or compliance must cite trusted evidence, and its dates and numbers must appear there.
3. `trusted_judge_evidence` shows the LLM judge only trusted text for such claims.
"""

import re
from collections.abc import Mapping

from trialsentinel.agents.evidence import Evidence
from trialsentinel.agents.verifier import (
    Claim,
    check_claim,
    cited_text,
    clean_ids,
    grounding_tokens,
    normalise_identifiers,
)

TRUSTED_KINDS = frozenset({"rule", "registry", "publication"})
UNTRUSTED_LABEL = "[untrusted quoted text]"

STATUS_TERMS = re.compile(
    r"\b(?:results?|posted|post(?:ing)?|submit\w*|submission|deadlines?|overdue|late|"
    r"complian\w*|non-?complian\w*|exempt\w*|applicab\w*|violat\w*|required|requirements?|"
    r"FDAAA|publish\w*|publications?|linked)\b",
    re.IGNORECASE,
)

REASON_PROVENANCE_CITE = (
    "rule D: a reporting-status claim must cite rule or registry evidence, not only excerpts"
)
REASON_PROVENANCE_GROUNDING = (
    "rule D: '{token}' appears only in untrusted excerpts, not in rule or registry evidence"
)

_HTML_TAG = re.compile(r"<[^>]{0,200}>")
_SPOOFED_ID = re.compile(r"\[\s*E\d+\s*\]", re.IGNORECASE)
_SPOOFED_KIND = re.compile(r"\(\s*(rule|registry|publication|excerpt)\s*\)", re.IGNORECASE)
_SPOOFED_LINK = re.compile(r"\bPubMed\s+(\d+)\s+is\s+linked\s+to\b", re.IGNORECASE)


def sanitise_excerpt(text: str) -> str:
    """Strips markup and defuses imitations of TrialSentinel's evidence format."""
    text = _HTML_TAG.sub(" ", text)
    text = _SPOOFED_ID.sub("[quoted id]", text)
    text = _SPOOFED_KIND.sub(r"(quoted \1)", text)
    text = _SPOOFED_LINK.sub(r"PubMed \1 is said by this quoted text to be linked to", text)
    text = re.sub(r"\s+", " ", text).strip()
    return f"{UNTRUSTED_LABEL} {text}"


def is_status_claim(text: str) -> bool:
    return bool(STATUS_TERMS.search(text))


def _trusted_cited(claim: Claim, evidence: Mapping[str, Evidence]) -> list[Evidence]:
    return [
        evidence[i]
        for i in clean_ids(claim.evidence_ids)
        if i in evidence and evidence[i].kind in TRUSTED_KINDS
    ]


def check_claim_v4(
    claim: Claim,
    evidence: Mapping[str, Evidence],
    *,
    subject_ids: frozenset[str] = frozenset(),
) -> str | None:
    """Verifier v3, then rule D (provenance) for reporting-status claims."""
    reason = check_claim(claim, evidence, subject_ids=subject_ids)
    if reason or not is_status_claim(claim.text):
        return reason
    trusted = _trusted_cited(claim, evidence)
    if not trusted:
        return REASON_PROVENANCE_CITE
    trusted_text = normalise_identifiers(" ".join(e.text for e in trusted)).replace(",", "")
    for token in grounding_tokens(claim.text):
        if token not in subject_ids and token not in trusted_text:
            return REASON_PROVENANCE_GROUNDING.format(token=token)
    return None


def trusted_judge_evidence(claim: Claim, evidence: Mapping[str, Evidence]) -> str:
    """For reporting-status claims the judge sees only trusted evidence."""
    if not is_status_claim(claim.text):
        return cited_text(claim, evidence)
    return " ".join(e.text for e in _trusted_cited(claim, evidence))
