from pathlib import Path

from trialsentinel.agents.labels import load_rows, score, write_rows
from trialsentinel.agents.offline import recheck, rejudge
from trialsentinel.agents.verifier import REASON_COMPLETION, REASON_PUBLICATION, check_text

REGISTRY = (
    "Registry record NCT01234567: overall status COMPLETED; primary completion 2012-03-01 (ACTUAL)."
)
LINK_SI = (
    "PubMed 111 is linked to NCT01234567 via pubmed_si, registry_derived; published 2020-01-01;"
)
LINK_RESULT = "PubMed 222 is linked to NCT01234567 via registry_result; published 2021-01-01;"
SUBJECT = frozenset({"NCT01234567"})


def test_rule_b_primary_completion_presented_as_completion() -> None:
    claim = "The trial NCT01234567 was completed as of 2012-03-01."
    assert check_text(claim, REGISTRY, subject_ids=SUBJECT) == REASON_COMPLETION


def test_rule_b_allows_primary_completion_wording() -> None:
    claim = "The trial was completed, with a primary completion date of 2012-03-01."
    assert check_text(claim, REGISTRY, subject_ids=SUBJECT) is None


def test_rule_c_linked_paper_presented_as_publication() -> None:
    claim = "The trial NCT01234567 was published in a journal article (PubMed ID 111)."
    assert check_text(claim, LINK_SI, subject_ids=SUBJECT) == REASON_PUBLICATION


def test_rule_c_reported_results_needs_registry_result() -> None:
    claim = "The publication 111 reported the results of the trial."
    assert check_text(claim, LINK_SI, subject_ids=SUBJECT) == REASON_PUBLICATION
    assert check_text("The publication 222 reported the results of the trial.", LINK_RESULT) is None


def test_rule_c_rejects_when_any_cited_link_is_not_a_result() -> None:
    claim = "The trial was published in two articles: 111 and 222."
    assert (
        check_text(claim, f"{LINK_SI} | {LINK_RESULT}", subject_ids=SUBJECT) == REASON_PUBLICATION
    )


def test_rule_c_does_not_fire_on_publication_dates_or_links() -> None:
    assert check_text("The publication 111 was published on 2020-01-01.", LINK_SI) is None
    assert check_text("The publication 111 is linked to the trial.", LINK_SI) is None


def _row(claim: str, cited: str, det: str = "pass", label: str = "") -> dict[str, str]:
    return {
        "nct_id": "NCT01234567",
        "round": "0",
        "decision": "accepted",
        "deterministic": det,
        "judge": "n/a",
        "claim": claim,
        "evidence_ids": "E1",
        "cited_text": cited,
        "human_supported": label,
    }


def test_recheck_applies_new_rules_and_keeps_structural_failures() -> None:
    rows = [
        _row("The trial was completed as of 2012-03-01.", REGISTRY),
        _row("No evidence.", "", det="cites no evidence"),
    ]
    assert recheck(rows) == 1
    assert rows[0]["deterministic"] == REASON_COMPLETION
    assert rows[1]["deterministic"] == "cites no evidence"


def test_rejudge_and_scoring_of_extra_judge_columns(tmp_path: Path) -> None:
    rows = [_row("good", "x", label="yes"), _row("bad", "x", label="no")]
    assert rejudge(rows, lambda claim, text: claim.text == "good", "judge_qwen") == 1
    path = tmp_path / "c.csv"
    write_rows(path, rows)
    report = score(load_rows(path))
    assert report["judge_qwen"]["catch_rate"] == 1.0
    assert report["deterministic+judge_qwen"]["false_rejection_rate"] == 0.0
