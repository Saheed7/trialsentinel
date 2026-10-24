from trialsentinel.guardrails.drug_terms import drop_non_drug_terms


def test_drops_comparators_and_normalises() -> None:
    raw = ["Placebo", "Metformin", "metformin ", "Normal Saline", "Insulin  Glargine"]
    assert drop_non_drug_terms(raw) == ["metformin", "insulin glargine"]


def test_empty_input_stays_empty() -> None:
    assert drop_non_drug_terms([]) == []
