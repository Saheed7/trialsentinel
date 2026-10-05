import copy

import pytest

from trialsentinel.ingestion.models import DatePrecision, stable_hash
from trialsentinel.ingestion.sources.clinicaltrials import parse_study

STUDY = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT01234567",
            "briefTitle": "A Study of Drug X in Asthma",
            "officialTitle": "A Phase 3 Randomised Trial of Drug X",
        },
        "statusModule": {
            "overallStatus": "COMPLETED",
            "startDateStruct": {"date": "2018-01"},
            "primaryCompletionDateStruct": {"date": "2020-06-15", "type": "ACTUAL"},
            "completionDateStruct": {"date": "2020-09", "type": "ACTUAL"},
            "lastUpdatePostDateStruct": {"date": "2023-02-01"},
        },
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Acme Pharma", "class": "INDUSTRY"}},
        "conditionsModule": {"conditions": ["Asthma"]},
        "designModule": {
            "studyType": "INTERVENTIONAL",
            "phases": ["PHASE3"],
            "enrollmentInfo": {"count": 420, "type": "ACTUAL"},
        },
        "armsInterventionsModule": {
            "interventions": [{"type": "DRUG", "name": "Drug X"}, {"type": "DRUG", "name": ""}]
        },
        "outcomesModule": {
            "primaryOutcomes": [{"measure": "Change in FEV1", "timeFrame": "12 weeks"}],
            "secondaryOutcomes": [{"measure": "Exacerbation rate", "timeFrame": "52 weeks"}],
        },
        "descriptionModule": {"briefSummary": "Tests Drug X."},
    },
    "hasResults": False,
}


def test_parse_study_maps_core_fields() -> None:
    record = parse_study(STUDY)
    assert record.nct_id == "NCT01234567"
    assert record.overall_status == "COMPLETED"
    assert record.phases == ["PHASE3"]
    assert record.lead_sponsor_name == "Acme Pharma"
    assert record.enrollment_count == 420
    assert record.has_results is False
    assert record.primary_completion_type == "ACTUAL"
    assert record.completion_date is not None
    assert record.completion_date.precision == DatePrecision.MONTH
    assert [o.kind for o in record.outcomes] == ["primary", "secondary"]
    assert [i.name for i in record.interventions] == ["Drug X"]  # unnamed dropped


def test_hash_is_order_independent() -> None:
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})


def test_hash_changes_when_content_changes() -> None:
    amended = copy.deepcopy(STUDY)
    amended["protocolSection"]["statusModule"]["overallStatus"] = "TERMINATED"
    assert parse_study(amended).raw_hash != parse_study(STUDY).raw_hash


def test_missing_nct_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="nctId"):
        parse_study({"protocolSection": {"identificationModule": {}}})
