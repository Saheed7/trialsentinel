"""Source-agnostic domain models produced by ingestion."""

from __future__ import annotations

import calendar
import hashlib
import json
from datetime import date
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def stable_hash(payload: Any) -> str:
    """Order-independent SHA-256 of a JSON-serialisable payload, used for change detection."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stable_bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DatePrecision(StrEnum):
    DAY = "day"
    MONTH = "month"
    YEAR = "year"


class PartialDate(BaseModel):
    """A registry date that may be 'YYYY-MM-DD', 'YYYY-MM', or 'YYYY'.

    Precision is kept explicitly: deadline rules must use `latest` to stay conservative,
    otherwise '2020-06' would be read as 1 June and inflate false positives.
    """

    model_config = ConfigDict(frozen=True)

    raw: str
    earliest: date
    precision: DatePrecision

    @property
    def latest(self) -> date:
        if self.precision is DatePrecision.DAY:
            return self.earliest
        if self.precision is DatePrecision.MONTH:
            last_day = calendar.monthrange(self.earliest.year, self.earliest.month)[1]
            return self.earliest.replace(day=last_day)
        return date(self.earliest.year, 12, 31)

    @classmethod
    def parse(cls, raw: str | None) -> PartialDate | None:
        if not raw or not raw.strip():
            return None
        text = raw.strip()
        try:
            numbers = [int(part) for part in text.split("-")]
        except ValueError:
            return None
        try:
            match numbers:
                case [y, m, d]:
                    return cls(raw=text, earliest=date(y, m, d), precision=DatePrecision.DAY)
                case [y, m]:
                    return cls(raw=text, earliest=date(y, m, 1), precision=DatePrecision.MONTH)
                case [y]:
                    return cls(raw=text, earliest=date(y, 1, 1), precision=DatePrecision.YEAR)
        except ValueError:
            return None
        return None


class LinkSource(StrEnum):
    """How a trial-publication link was discovered (evidence provenance)."""

    REGISTRY_RESULT = "registry_result"
    REGISTRY_BACKGROUND = "registry_background"
    REGISTRY_DERIVED = "registry_derived"
    REGISTRY_OTHER = "registry_other"
    PUBMED_SECONDARY_ID = "pubmed_si"
    PUBMED_TEXT = "pubmed_tiab"


REGISTRY_LINK_SOURCES: tuple[str, ...] = tuple(
    s.value for s in LinkSource if s.value.startswith("registry_")
)

_REGISTRY_TYPE_TO_SOURCE = {
    "RESULT": LinkSource.REGISTRY_RESULT,
    "BACKGROUND": LinkSource.REGISTRY_BACKGROUND,
    "DERIVED": LinkSource.REGISTRY_DERIVED,
}


def registry_link_source(reference_type: str | None) -> LinkSource:
    return _REGISTRY_TYPE_TO_SOURCE.get((reference_type or "").upper(), LinkSource.REGISTRY_OTHER)


class OutcomeMeasure(BaseModel):
    kind: Literal["primary", "secondary"]
    position: int
    measure: str
    description: str | None = None
    time_frame: str | None = None


class Intervention(BaseModel):
    type: str | None = None
    name: str
    position: int


class TrialReference(BaseModel):
    pmid: str
    type: str | None = None
    citation: str | None = None


class TrialRecord(BaseModel):
    nct_id: str
    brief_title: str
    official_title: str | None = None
    overall_status: str | None = None
    study_type: str | None = None
    phases: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    lead_sponsor_name: str | None = None
    lead_sponsor_class: str | None = None
    enrollment_count: int | None = None
    start_date: PartialDate | None = None
    primary_completion_date: PartialDate | None = None
    primary_completion_type: str | None = None
    completion_date: PartialDate | None = None
    results_first_submit_date: PartialDate | None = None
    results_first_post_date: PartialDate | None = None
    last_update_post_date: PartialDate | None = None
    has_results: bool = False
    brief_summary: str | None = None
    outcomes: list[OutcomeMeasure] = Field(default_factory=list)
    interventions: list[Intervention] = Field(default_factory=list)
    references: list[TrialReference] = Field(default_factory=list)
    raw_hash: str


class PublicationRecord(BaseModel):
    pmid: str
    title: str
    abstract: str | None = None
    journal: str | None = None
    pub_date: PartialDate | None = None
    doi: str | None = None
    publication_types: list[str] = Field(default_factory=list)
    registry_ids: list[str] = Field(default_factory=list)  # NCT IDs from PubMed DataBankList
    raw_hash: str
