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


def test_calibration_answer_key_scores_full_marks_and_passes() -> None:
    from trialsentinel.agents.labels import CALIBRATION, calibrate

    key = iter(item["answer"] for item in CALIBRATION)
    result = calibrate(ask=lambda _: next(key), show=lambda _: None)
    assert result == {"correct": len(CALIBRATION), "items": len(CALIBRATION), "passed": True}


def test_calibration_fails_when_answering_yes_to_everything() -> None:
    from trialsentinel.agents.labels import calibrate

    result = calibrate(ask=lambda _: "y", show=lambda _: None)
    assert result["passed"] is False


def test_calibration_items_are_fictional_and_balanced() -> None:
    from trialsentinel.agents.labels import CALIBRATION

    answers = [item["answer"] for item in CALIBRATION]
    assert answers.count("y") == answers.count("n")
    assert all("NCT0999" in item["cited"] for item in CALIBRATION)
