"""Deterministic post-filter for LLM drug extractions.

Schema validation catches malformed output but not schema-valid wrong answers
(for example, a model listing "placebo" as a drug). Rules catch the known cases.
"""

from collections.abc import Iterable

from trialsentinel.ingestion.sources.openfda import NON_DRUG_MARKERS


def drop_non_drug_terms(terms: Iterable[str]) -> list[str]:
    """Lowercase, collapse whitespace, de-duplicate, and drop comparator terms."""
    kept: list[str] = []
    for raw in terms:
        term = " ".join(str(raw).lower().split())
        if term and term not in kept and not any(m in term for m in NON_DRUG_MARKERS):
            kept.append(term)
    return kept
