"""Provenance defences against injected evidence (red-team defended arm, verifier "v4").

Excerpts are free text written by sponsors and authors, so an attacker can plant text in them.
Grounding a claim in its cited text then proves nothing: the planted text is the citation.
These defences separate trusted evidence (built by TrialSentinel from structured database
fields: rule, registry, publication) from untrusted excerpts:

1. `sanitise_excerpt` neutralises text that imitates TrialSentinel's own evidence format
   (evidence IDs, kind markers, publication-link lines) and labels the excerpt as untrusted.
2. `check_claim_v4` adds rule D: a claim about reporting status, deadlines, results, exemption
   or compliance must cite trusted evidence, and its dates and numbers must appear there.
   Rule D v2 narrows the status terms and checks that PubMed and trial IDs are trusted.
3. `trusted_judge_evidence` shows the LLM judge only trusted text for such claims.
"""

import re
from collections.abc import Mapping

from trialsentinel.agents.evidence import Evidence
from trialsentinel.agents.sanitise import UNTRUSTED_LABEL, sanitise_excerpt
from trialsentinel.agents.verifier import (
    Claim,
    check_claim,
    cited_text,
    clean_ids,
    grounding_tokens,
    normalise_identifiers,
)

__all__ = [
    "RULE_D_VERSIONS",
    "TRUSTED_KINDS",
    "UNTRUSTED_LABEL",
    "check_claim_v4",
    "is_status_claim",
    "sanitise_excerpt",
    "trusted_judge_evidence",
]

TRUSTED_KINDS = frozenset({"rule", "registry", "publication"})

# Rule D v1 (round 1): over-blocked claims about a paper's findings ("publication", "reported").
STATUS_TERMS_V1 = re.compile(
    r"\b(?:results?|posted|post(?:ing)?|submit\w*|submission|deadlines?|overdue|late|"
    r"complian\w*|non-?complian\w*|exempt\w*|applicab\w*|violat\w*|required|requirements?|"
    r"FDAAA|publish\w*|publications?|linked)\b",
    re.IGNORECASE,
)
# Rule D v2 (round 2): FDAAA reporting-obligation terms only.
STATUS_TERMS_V2 = re.compile(
    r"\b(?:posted|post(?:ing)?|submit\w*|submission|deadlines?|overdue|late|"
    r"complian\w*|non-?complian\w*|exempt\w*|applicab\w*|violat\w*|required|requirements?|"
    r"obligations?|FDAAA|ClinicalTrials\.gov|registry_result)\b",
    re.IGNORECASE,
)
STATUS_TERMS = {1: STATUS_TERMS_V1, 2: STATUS_TERMS_V2}
RULE_D_VERSIONS = tuple(STATUS_TERMS)

REASON_PROVENANCE_CITE = (
    "rule D: a reporting-status claim must cite rule or registry evidence, not only excerpts"
)
REASON_PROVENANCE_GROUNDING = (
    "rule D: '{token}' appears only in untrusted excerpts, not in rule or registry evidence"
)
REASON_PROVENANCE_IDENTIFIER = (
    "rule D: identifier '{token}' does not appear in any trusted evidence for this trial"
)

# PubMed IDs (7-8 digits) and trial IDs; dates and ordinary counts are shorter or hyphenated.
_IDENTIFIER_TOKEN = re.compile(r"\b(?:NCT\d{8}|\d{7,8})\b")


def is_status_claim(text: str, version: int = 1) -> bool:
    return bool(STATUS_TERMS[version].search(text))


def _all_trusted_text(evidence: Mapping[str, Evidence]) -> str:
    return normalise_identifiers(
        " ".join(e.text for e in evidence.values() if e.kind in TRUSTED_KINDS)
    )


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
    rule_d_version: int = 1,
) -> str | None:
    """Verifier v3, then rule D (provenance).

    v1: reporting-status claims (broad term list) must cite trusted evidence and be grounded
    in it. v2: a narrower, obligation-only term list, plus an identifier check on every claim:
    a PubMed or trial ID must appear somewhere in the trial's trusted evidence."""
    reason = check_claim(claim, evidence, subject_ids=subject_ids)
    if reason:
        return reason
    if rule_d_version >= 2:
        trusted_all = _all_trusted_text(evidence)
        for token in _IDENTIFIER_TOKEN.findall(normalise_identifiers(claim.text)):
            if token not in subject_ids and token not in trusted_all:
                return REASON_PROVENANCE_IDENTIFIER.format(token=token)
    if not is_status_claim(claim.text, rule_d_version):
        return None
    trusted = _trusted_cited(claim, evidence)
    if not trusted:
        return REASON_PROVENANCE_CITE
    trusted_text = normalise_identifiers(" ".join(e.text for e in trusted)).replace(",", "")
    for token in grounding_tokens(claim.text):
        if token not in subject_ids and token not in trusted_text:
            return REASON_PROVENANCE_GROUNDING.format(token=token)
    return None


def trusted_judge_evidence(
    claim: Claim, evidence: Mapping[str, Evidence], *, rule_d_version: int = 1
) -> str:
    """For reporting-status claims the judge sees only trusted evidence."""
    if not is_status_claim(claim.text, rule_d_version):
        return cited_text(claim, evidence)
    return " ".join(e.text for e in _trusted_cited(claim, evidence))
