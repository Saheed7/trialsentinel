from collections.abc import Sequence

from trialsentinel.agents.evidence import Case, Evidence, build_evidence
from trialsentinel.agents.missing_results import build_graph
from trialsentinel.agents.verifier import Claim, Draft, check_claim, grounding_tokens

EVIDENCE = {
    "E1": Evidence("E1", "rule", "Effective deadline 2021-04-21; days overdue 1993."),
    "E2": Evidence("E2", "registry", "Registry record NCT03317028: results posted: no."),
}


def _case() -> Case:
    return Case(
        nct_id="NCT03317028",
        brief_title="Drug X",
        overall_status="COMPLETED",
        primary_completion="2020-04-21",
        primary_completion_type="ACTUAL",
        results_first_submitted=None,
        has_results=False,
        lead_sponsor="Acme",
        sponsor_class="INDUSTRY",
        as_of="2026-10-05",
        applicability="likely_act",
        applicability_confidence="high",
        reporting_status="overdue",
        standard_deadline="2021-04-21",
        effective_deadline="2021-04-21",
        days_overdue=1993,
        days_late=None,
        finding="results_overdue",
        basis=("interventional",),
        publications=(),
    )


def test_grounding_tokens_extracts_dates_numbers_and_ids() -> None:
    tokens = grounding_tokens("NCT03317028 was 1,993 days late after 2021-04-21.")
    assert "NCT03317028" in tokens and "1993" in tokens and "2021-04-21" in tokens


def test_claim_with_supported_facts_passes() -> None:
    claim = Claim(text="Results are 1993 days overdue.", evidence_ids=["E1"])
    assert check_claim(claim, EVIDENCE) is None


def test_bracketed_ids_are_tolerated() -> None:
    assert check_claim(Claim(text="No results posted.", evidence_ids=["[E2]"]), EVIDENCE) is None


def test_invented_number_is_rejected() -> None:
    claim = Claim(text="Results are 2000 days overdue.", evidence_ids=["E1"])
    assert "2000" in (check_claim(claim, EVIDENCE) or "")


def test_number_must_be_in_the_cited_item_not_elsewhere() -> None:
    claim = Claim(text="Results are 1993 days overdue.", evidence_ids=["E2"])
    assert check_claim(claim, EVIDENCE) is not None


def test_missing_and_unknown_citations_are_rejected() -> None:
    assert check_claim(Claim(text="Overdue.", evidence_ids=[]), EVIDENCE) == "cites no evidence"
    assert "unknown" in (check_claim(Claim(text="Overdue.", evidence_ids=["E9"]), EVIDENCE) or "")


def test_build_evidence_numbers_items_and_reports_absent_papers() -> None:
    items = build_evidence(_case(), [])
    assert [e.id for e in items] == ["E1", "E2", "E3"]
    assert "2021-04-21" in items[0].text
    assert "No PubMed publication" in items[2].text


def _scripted(*drafts: Draft):
    calls: list[str | None] = []

    def drafter(verdict: str, evidence: Sequence[Evidence], feedback: str | None) -> Draft:
        calls.append(feedback)
        return drafts[min(len(calls) - 1, len(drafts) - 1)]

    return drafter, calls


def _run(graph) -> dict:  # type: ignore[no-untyped-def]
    return graph.invoke(
        {
            "nct_id": "NCT03317028",
            "verdict": "results_overdue",
            "evidence": list(EVIDENCE.values()),
            "revisions": 0,
            "rejected": [],
            "rounds": [],
        }
    )


def test_bad_draft_is_revised_then_verified() -> None:
    bad = Draft(claims=[Claim(text="Results are 2000 days overdue.", evidence_ids=["E1"])])
    good = Draft(claims=[Claim(text="Results are 1993 days overdue.", evidence_ids=["E1"])])
    drafter, calls = _scripted(bad, good)
    state = _run(build_graph(drafter))
    assert state["status"] == "verified"
    assert state["revisions"] == 1
    assert [r["rejected"] for r in state["rounds"]] == [1, 0]
    assert calls[0] is None and "2000" in (calls[1] or "")  # feedback names the problem


def test_persistently_bad_draft_ends_in_review_after_one_revision() -> None:
    bad = Draft(claims=[Claim(text="Overdue by 9999 days.", evidence_ids=["E1"])])
    drafter, calls = _scripted(bad)
    state = _run(build_graph(drafter))
    assert state["status"] == "needs_review"
    assert len(calls) == 2


def test_judge_can_veto_a_claim_that_passes_deterministic_checks() -> None:
    claim = Claim(text="The sponsor posted full results.", evidence_ids=["E2"])
    drafter, _ = _scripted(Draft(claims=[claim]))
    state = _run(build_graph(drafter, judge=lambda c, text: False))
    assert state["status"] == "needs_review"
    assert any("judge" in r["reason"] for r in state["rejected"])
