from trialsentinel.agents.verifier import (
    REASON_PUBLICATION,
    VERIFIER_VERSION,
    check_text,
    grounding_tokens,
    normalise_identifiers,
)

NO_LINK = "No PubMed publication is linked to NCT05363670 by any route."
LINK_SI = (
    "PubMed 111 is linked to NCT01234567 via pubmed_si, registry_derived; published 2020-01-01;"
)
EXCERPT = "The VITamin D and OmegA-3 TriaL (VITAL; NCT 01169259) is an ongoing randomized trial."
SUBJECT = frozenset({"NCT05363670"})


def test_version_is_3() -> None:
    assert VERIFIER_VERSION == 3


def test_spaced_trial_ids_are_normalised() -> None:
    assert normalise_identifiers("VITAL; NCT 01169259 and NCT-01234567") == (
        "VITAL; NCT01169259 and NCT01234567"
    )
    assert "NCT01169259" in grounding_tokens("an ancillary study of NCT 01169259")


def test_claim_matches_evidence_with_spaced_id() -> None:
    claim = "The trial NCT01633177 was an ancillary study of the trial NCT01169259."
    assert check_text(claim, EXCERPT, subject_ids=frozenset({"NCT01633177"})) is None


def test_rule_c_ignores_negated_publication() -> None:
    claim = "The trial NCT05363670 has not been published in PubMed."
    assert check_text(claim, NO_LINK, subject_ids=SUBJECT) is None
    claim = "The trial NCT05363670 was never published in a journal."
    assert check_text(claim, NO_LINK, subject_ids=SUBJECT) is None


def test_rule_c_ignores_negated_results_report() -> None:
    claim = "The publication 111 does not report the results of the trial."
    assert check_text(claim, LINK_SI) is None


def test_rule_c_still_fires_on_positive_claims() -> None:
    claim = "The trial NCT01234567 was published in a journal article (PubMed ID 111)."
    assert check_text(claim, LINK_SI, subject_ids=frozenset({"NCT01234567"})) == REASON_PUBLICATION
    claim = "The publication 111 reports the results of the trial."
    assert check_text(claim, LINK_SI) == REASON_PUBLICATION


def test_negation_in_an_earlier_clause_does_not_hide_a_positive_claim() -> None:
    claim = "Results were not posted, but the trial was published in a journal (PubMed ID 111)."
    assert check_text(claim, LINK_SI, subject_ids=frozenset({"NCT01234567"})) == REASON_PUBLICATION
