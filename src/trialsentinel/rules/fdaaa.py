"""FDAAA 801 / 42 CFR Part 11: probable applicability and results-reporting status.

Design rule: a trial is flagged only when every uncertain input resolves against it.
- Applicability needs positive evidence. IND/IDE status is not public, so outcomes are
  'likely_act', 'not_act', or 'undetermined', each with an explicit basis.
- Deadlines use the latest calendar date consistent with a partial registry date.
- A certification or extension filed before the deadline defers it. Extension terms are
  not public and the registry does not distinguish the two, so the deferred deadline is
  an estimate and is marked as such.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from trialsentinel.ingestion.models import DatePrecision, PartialDate

RULE_VERSION = 1
FDAAA_EFFECTIVE = date(2007, 9, 27)
FDAAA_ONGOING_CUTOFF = date(2007, 12, 26)
PHASE1_ONLY = frozenset({"EARLY_PHASE1", "PHASE1"})
DRUG_TYPES = frozenset({"DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT"})
DEVICE_TYPES = frozenset({"DEVICE", "COMBINATION_PRODUCT"})


class Applicability(StrEnum):
    LIKELY_ACT = "likely_act"
    NOT_ACT = "not_act"
    UNDETERMINED = "undetermined"


class Confidence(StrEnum):
    HIGH = "high"
    LOW = "low"


class ReportingStatus(StrEnum):
    UNKNOWN = "unknown"
    NOT_DUE = "not_due"
    NOT_YET_DUE = "not_yet_due"
    REPORTED_ON_TIME = "reported_on_time"
    REPORTED_LATE = "reported_late"
    OVERDUE = "overdue"


class Finding(StrEnum):
    RESULTS_OVERDUE = "results_overdue"
    RESULTS_REPORTED_LATE = "results_reported_late"


def add_years(d: date, years: int) -> date:
    """Calendar-year addition. 29 Feb maps to 1 Mar: the later date, so conservative."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return date(d.year + years, 3, 1)


def latest_possible(earliest: date | None, precision: str | None) -> date | None:
    if earliest is None:
        return None
    if precision is None:
        return earliest
    return PartialDate(raw="", earliest=earliest, precision=DatePrecision(precision)).latest


@dataclass(frozen=True)
class TrialFacts:
    nct_id: str
    overall_status: str | None = None
    study_type: str | None = None
    phases: Sequence[str] = ()
    intervention_types: Sequence[str] = ()
    is_fda_regulated_drug: bool | None = None
    is_fda_regulated_device: bool | None = None
    is_us_export: bool | None = None
    has_us_site: bool = False
    start_date_latest: date | None = None
    primary_completion_latest: date | None = None
    primary_completion_type: str | None = None
    results_first_submit_date: date | None = None
    disp_first_submit_date: date | None = None


@dataclass(frozen=True)
class ApplicabilityResult:
    status: Applicability
    confidence: Confidence
    basis: tuple[str, ...]


@dataclass(frozen=True)
class ReportingResult:
    status: ReportingStatus
    standard_deadline: date | None = None
    effective_deadline: date | None = None
    deadline_extended: bool = False
    days_overdue: int | None = None
    days_late: int | None = None
    basis: tuple[str, ...] = ()


def assess_applicability(f: TrialFacts) -> ApplicabilityResult:
    if (f.study_type or "").upper() != "INTERVENTIONAL":
        return ApplicabilityResult(
            Applicability.NOT_ACT, Confidence.HIGH, ("study is not interventional",)
        )

    phases = {p.upper() for p in f.phases}
    types = {t.upper() for t in f.intervention_types}
    device_like = bool(types & DEVICE_TYPES)
    if phases and phases <= PHASE1_ONLY and not device_like:
        return ApplicabilityResult(
            Applicability.NOT_ACT, Confidence.HIGH, ("phase 1 drug trials are excluded",)
        )

    if (
        f.start_date_latest is not None
        and f.start_date_latest < FDAAA_EFFECTIVE
        and f.primary_completion_latest is not None
        and f.primary_completion_latest < FDAAA_ONGOING_CUTOFF
    ):
        return ApplicabilityResult(
            Applicability.NOT_ACT, Confidence.HIGH, ("completed before FDAAA 801 took effect",)
        )

    if f.is_fda_regulated_drug is not None or f.is_fda_regulated_device is not None:
        if not (f.is_fda_regulated_drug or f.is_fda_regulated_device):
            return ApplicabilityResult(
                Applicability.NOT_ACT,
                Confidence.HIGH,
                ("does not study an FDA-regulated drug or device product",),
            )
        if f.has_us_site or f.is_us_export:
            where = "has a US site" if f.has_us_site else "product exported from the US"
            return ApplicabilityResult(
                Applicability.LIKELY_ACT,
                Confidence.HIGH,
                ("interventional", "studies an FDA-regulated product", where),
            )
        return ApplicabilityResult(
            Applicability.UNDETERMINED,
            Confidence.LOW,
            ("FDA-regulated product without a US site; IND/IDE status is not public",),
        )

    # Older records predate the oversight data elements: fall back to a weaker heuristic.
    if not (types & (DRUG_TYPES | DEVICE_TYPES)):
        return ApplicabilityResult(
            Applicability.NOT_ACT, Confidence.LOW, ("no drug, biological, or device intervention",)
        )
    if f.has_us_site:
        return ApplicabilityResult(
            Applicability.LIKELY_ACT,
            Confidence.LOW,
            ("legacy record: product intervention with a US site (heuristic)",),
        )
    return ApplicabilityResult(
        Applicability.UNDETERMINED,
        Confidence.LOW,
        ("legacy record without a US site; applicability cannot be inferred",),
    )


def assess_reporting(f: TrialFacts, *, as_of: date) -> ReportingResult:
    if (f.overall_status or "").upper() == "WITHDRAWN":
        return ReportingResult(ReportingStatus.NOT_DUE, basis=("withdrawn before enrolment",))
    if f.primary_completion_latest is None:
        return ReportingResult(ReportingStatus.UNKNOWN, basis=("no primary completion date",))
    if (f.primary_completion_type or "").upper() != "ACTUAL":
        return ReportingResult(
            ReportingStatus.NOT_DUE, basis=("primary completion date is anticipated",)
        )

    standard = add_years(f.primary_completion_latest, 1)
    effective, extended = standard, False
    basis = [f"standard deadline: latest possible primary completion + 1 year = {standard}"]
    if f.disp_first_submit_date is not None:
        if f.disp_first_submit_date <= standard:
            effective = max(standard, add_years(f.disp_first_submit_date, 2))
            extended = True
            basis.append(
                f"certification/extension filed {f.disp_first_submit_date}; deadline estimated "
                f"as filing + 2 years = {effective} (extension terms are not public)"
            )
        else:
            basis.append("certification/extension filed after the standard deadline (untimely)")

    if f.results_first_submit_date is not None:
        days_late = (f.results_first_submit_date - effective).days
        status = (
            ReportingStatus.REPORTED_LATE if days_late > 0 else ReportingStatus.REPORTED_ON_TIME
        )
        return ReportingResult(
            status,
            standard,
            effective,
            extended,
            days_late=max(days_late, 0),
            basis=tuple(basis),
        )
    if as_of > effective:
        return ReportingResult(
            ReportingStatus.OVERDUE,
            standard,
            effective,
            extended,
            days_overdue=(as_of - effective).days,
            basis=tuple(basis),
        )
    return ReportingResult(
        ReportingStatus.NOT_YET_DUE, standard, effective, extended, basis=tuple(basis)
    )


def classify(app: ApplicabilityResult, rep: ReportingResult) -> Finding | None:
    if app.status is not Applicability.LIKELY_ACT:
        return None
    if rep.status is ReportingStatus.OVERDUE:
        return Finding.RESULTS_OVERDUE
    if rep.status is ReportingStatus.REPORTED_LATE:
        return Finding.RESULTS_REPORTED_LATE
    return None
