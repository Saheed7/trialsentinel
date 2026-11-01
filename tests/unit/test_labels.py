from pathlib import Path

import pytest

from trialsentinel.agents.labels import load_rows, score


def _row(det: str, judge: str, decision: str, human: str) -> dict[str, str]:
    return {"deterministic": det, "judge": judge, "decision": decision, "human_supported": human}


def test_score_against_human_labels() -> None:
    rows = [
        _row("pass", "supported", "accepted", "yes"),  # correct accept
        _row("pass", "unsupported", "rejected", "yes"),  # judge false rejection
        _row("'2000' missing", "unsupported", "rejected", "no"),  # both catch
        _row("pass", "unsupported", "rejected", "no"),  # only the judge catches
        _row("pass", "supported", "accepted", "no"),  # both miss
        _row("pass", "supported", "accepted", ""),  # unlabelled: ignored
    ]
    report = score(rows)
    assert report["claims_labelled"] == 5
    assert report["draft_hallucination_rate"] == 0.6
    assert report["deterministic"]["catch_rate"] == 0.333
    assert report["judge"]["catch_rate"] == 0.667
    assert report["judge"]["false_rejection_rate"] == 0.5
    assert report["deterministic+judge"]["catch_rate"] == 0.667
    assert report["deterministic+judge"]["residual_hallucination_rate"] == 0.5


def test_unlabelled_file_gives_a_hint() -> None:
    assert "hint" in score([_row("pass", "n/a", "accepted", "")])


def test_duplicate_columns_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "claims.csv"
    path.write_text("decision,claim,decision,human_supported\nx,y,z,yes\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_rows(path)


def test_missing_label_column_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "claims.csv"
    path.write_text("decision,claim\nx,y\n", encoding="utf-8")
    with pytest.raises(ValueError, match="human_supported"):
        load_rows(path)


def test_identical_claims_are_asked_once_and_labelled_together(tmp_path: Path) -> None:
    from trialsentinel.agents.labels import clear_labels, interactive_label, write_rows

    base = {
        "nct_id": "N",
        "round": "0",
        "decision": "accepted",
        "deterministic": "pass",
        "judge": "n/a",
        "evidence_ids": "E1",
        "human_supported": "",
    }
    rows = [
        base | {"claim": "A", "cited_text": "x"},
        base | {"claim": "B", "cited_text": "y"},
        base | {"claim": "A", "cited_text": "x"},
    ]
    path = tmp_path / "c.csv"
    write_rows(path, rows)
    answers = iter(["y", "n"])
    result = interactive_label(path, ask=lambda _: next(answers), show=lambda _: None)
    labels = [r["human_supported"] for r in load_rows(path)]
    assert labels == ["yes", "no", "yes"]
    assert result["labelled_now"] == 2
    assert clear_labels(path) == 3
    assert all(r["human_supported"] == "" for r in load_rows(path))


@pytest.mark.parametrize("set_name", ["a", "b"])
def test_calibration_answer_key_scores_full_marks_and_passes(set_name: str) -> None:
    from trialsentinel.agents.labels import CALIBRATION_SETS, calibrate

    items = CALIBRATION_SETS[set_name]
    key = iter(item["answer"] for item in items)
    result = calibrate(set_name, ask=lambda _: next(key), show=lambda _: None)
    assert result == {"set": set_name, "correct": len(items), "items": len(items), "passed": True}


@pytest.mark.parametrize("set_name", ["a", "b"])
@pytest.mark.parametrize("always", ["y", "n"])
def test_calibration_fails_when_giving_the_same_answer_to_everything(
    set_name: str, always: str
) -> None:
    from trialsentinel.agents.labels import calibrate

    result = calibrate(set_name, ask=lambda _: always, show=lambda _: None)
    assert result["passed"] is False


@pytest.mark.parametrize("set_name", ["a", "b"])
def test_calibration_items_are_fictional_and_balanced(set_name: str) -> None:
    from trialsentinel.agents.labels import CALIBRATION_SETS

    items = CALIBRATION_SETS[set_name]
    answers = [item["answer"] for item in items]
    assert answers.count("y") == answers.count("n")
    assert all("NCT0999" in item["cited"] for item in items)


def test_calibration_sets_do_not_share_items() -> None:
    from trialsentinel.agents.labels import CALIBRATION, CALIBRATION_B

    assert not {i["claim"] for i in CALIBRATION} & {i["claim"] for i in CALIBRATION_B}


def _labelled(claim: str, label: str) -> dict[str, str]:
    return {"claim": claim, "cited_text": "x", "human_supported": label}


def test_agreement_kappa_matches_a_hand_computed_value() -> None:
    from trialsentinel.agents.labels import agreement

    a = [_labelled(c, lab) for c, lab in zip("ABCDEFGHIJ", ["yes"] * 6 + ["no"] * 4, strict=True)]
    b_labels = ["yes"] * 5 + ["no"] + ["no"] * 3 + ["yes"]
    b = [_labelled(c, lab) for c, lab in zip("ABCDEFGHIJ", b_labels, strict=True)]
    result = agreement(a, list(reversed(b)))  # row order must not matter
    # observed 0.8; expected 0.6*0.6 + 0.4*0.4 = 0.52; kappa = 0.28 / 0.48 = 0.583
    assert result["items_compared"] == 10
    assert result["observed_agreement"] == 0.8
    assert result["cohens_kappa"] == 0.583
    assert {d["claim"] for d in result["disagreements"]} == {"F", "J"}


def test_agreement_ignores_unlabelled_and_unmatched_claims() -> None:
    from trialsentinel.agents.labels import agreement

    a = [_labelled("A", "yes"), _labelled("B", ""), _labelled("C", "no")]
    b = [_labelled("A", "yes"), _labelled("B", "no"), _labelled("Z", "no")]
    assert agreement(a, b)["items_compared"] == 1
    assert agreement([_labelled("A", "")], b)["items_compared"] == 0
