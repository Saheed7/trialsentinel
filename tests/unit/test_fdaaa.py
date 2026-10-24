from datetime import date

from trialsentinel.rules.fdaaa import (
    Applicability,
    Confidence,
    Finding,
    ReportingStatus,
    TrialFacts,
    add_years,
    assess_applicability,
    assess_reporting,
    classify,
    latest_possible,
)

AS_OF = date(2026, 10, 5)


def facts(**overrides: object) -> TrialFacts:
    base: dict[str, object] = {
        "nct_id": "NCT01234567",
        "overall_status": "COMPLETED",
        "study_type": "INTERVENTIONAL",
        "phases": ("PHASE3",),
        "intervention_types": ("DRUG",),
        "is_fda_regulated_drug": True,
        "is_fda_regulated_device": False,
        "has_us_site": True,
        "start_date_latest": date(2019, 1, 31),
        "primary_completion_latest": date(2021, 6, 30),
        "primary_completion_type": "ACTUAL",
    }
    base.update(overrides)
    return TrialFacts(**base)  # type: ignore[arg-type]


# ---- applicability ----


def test_regulated_drug_with_us_site_is_likely_act() -> None:
    result = assess_applicability(facts())
    assert result.status is Applicability.LIKELY_ACT
    assert result.confidence is Confidence.HIGH


def test_observational_study_is_not_act() -> None:
    assert assess_applicability(facts(study_type="OBSERVATIONAL")).status is Applicability.NOT_ACT


def test_phase1_drug_trial_is_excluded_but_phase1_2_is_not() -> None:
    assert assess_applicability(facts(phases=("PHASE1",))).status is Applicability.NOT_ACT
    combined = assess_applicability(facts(phases=("PHASE1", "PHASE2")))
    assert combined.status is Applicability.LIKELY_ACT


def test_not_fda_regulated_is_not_act() -> None:
    result = assess_applicability(facts(is_fda_regulated_drug=False))
    assert result.status is Applicability.NOT_ACT


def test_regulated_without_us_site_is_undetermined() -> None:
    result = assess_applicability(facts(has_us_site=False))
    assert result.status is Applicability.UNDETERMINED


def test_us_export_counts_like_a_us_site() -> None:
    result = assess_applicability(facts(has_us_site=False, is_us_export=True))
    assert result.status is Applicability.LIKELY_ACT


def test_legacy_record_uses_low_confidence_heuristic() -> None:
    legacy = facts(is_fda_regulated_drug=None, is_fda_regulated_device=None)
    result = assess_applicability(legacy)
    assert result.status is Applicability.LIKELY_ACT
    assert result.confidence is Confidence.LOW


def test_pre_fdaaa_trial_is_not_act() -> None:
    old = facts(start_date_latest=date(2005, 1, 31), primary_completion_latest=date(2007, 6, 30))
    assert assess_applicability(old).status is Applicability.NOT_ACT


# ---- dates ----


def test_month_precision_uses_end_of_month() -> None:
    assert latest_possible(date(2020, 6, 1), "month") == date(2020, 6, 30)
    assert latest_possible(date(2020, 1, 1), "year") == date(2020, 12, 31)


def test_leap_day_rolls_forward_conservatively() -> None:
    assert add_years(date(2020, 2, 29), 1) == date(2021, 3, 1)


# ---- reporting ----


def test_partial_date_avoids_false_late_finding() -> None:
    # Registry says "2020-06". Results submitted 2021-06-20 are on time against the latest
    # possible deadline (2021-06-30), but would look late if "2020-06" were read as 1 June.
    f = facts(
        primary_completion_latest=latest_possible(date(2020, 6, 1), "month"),
        results_first_submit_date=date(2021, 6, 20),
    )
    assert assess_reporting(f, as_of=AS_OF).status is ReportingStatus.REPORTED_ON_TIME


def test_overdue_counts_days_past_deadline() -> None:
    result = assess_reporting(facts(), as_of=AS_OF)
    assert result.status is ReportingStatus.OVERDUE
    assert result.effective_deadline == date(2022, 6, 30)
    assert result.days_overdue == (AS_OF - date(2022, 6, 30)).days


def test_reported_late_records_days_late() -> None:
    result = assess_reporting(facts(results_first_submit_date=date(2022, 7, 10)), as_of=AS_OF)
    assert result.status is ReportingStatus.REPORTED_LATE
    assert result.days_late == 10


def test_timely_certification_defers_deadline() -> None:
    f = facts(disp_first_submit_date=date(2022, 5, 1), primary_completion_latest=date(2025, 6, 30))
    result = assess_reporting(f, as_of=AS_OF)
    assert result.deadline_extended
    assert result.effective_deadline == date(2026, 6, 30)  # max(standard, filing + 2y)


def test_untimely_certification_does_not_defer() -> None:
    result = assess_reporting(facts(disp_first_submit_date=date(2023, 1, 1)), as_of=AS_OF)
    assert not result.deadline_extended
    assert result.status is ReportingStatus.OVERDUE


def test_anticipated_completion_is_not_due() -> None:
    result = assess_reporting(facts(primary_completion_type="ESTIMATED"), as_of=AS_OF)
    assert result.status is ReportingStatus.NOT_DUE


def test_withdrawn_trial_is_not_due() -> None:
    result = assess_reporting(facts(overall_status="WITHDRAWN"), as_of=AS_OF)
    assert result.status is ReportingStatus.NOT_DUE


# ---- classification ----


def test_finding_requires_likely_act() -> None:
    overdue = assess_reporting(facts(), as_of=AS_OF)
    assert classify(assess_applicability(facts()), overdue) is Finding.RESULTS_OVERDUE
    not_act = assess_applicability(facts(study_type="OBSERVATIONAL"))
    assert classify(not_act, overdue) is None
