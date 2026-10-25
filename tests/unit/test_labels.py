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
    assert report["combined"]["residual_hallucination_rate"] == 0.5


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
