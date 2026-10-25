"""Deterministic post-filter for LLM drug extractions.

Schema validation catches malformed output but not schema-valid wrong answers
(for example, a model listing "placebo" as a drug). Rules catch the known cases.
"""

from collections.abc import Iterable

from trialsentinel.ingestion.sources.openfda import NON_DRUG_MARKERS, normalize_drug_terms


def drop_non_drug_terms(terms: Iterable[str]) -> list[str]:
    """Lowercase, collapse whitespace, de-duplicate, and drop comparator terms."""
    kept: list[str] = []
    for raw in terms:
        term = " ".join(str(raw).lower().split())
        if term and term not in kept and not any(m in term for m in NON_DRUG_MARKERS):
            kept.append(term)
    return kept


def normalise_llm_drugs(terms: Iterable[str]) -> list[str]:
    """Pass each LLM-extracted term through the deterministic ingestion normaliser.

    Removes doses, units, dosage forms, and bracketed brand names, and drops comparators,
    so LLM output and rule output share one canonical form.
    """
    kept: list[str] = []
    for raw in terms:
        for term in normalize_drug_terms(str(raw)):
            if term not in kept:
                kept.append(term)
    return kept
