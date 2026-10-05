"""ClinicalTrials.gov API v2: paginated retrieval and normalisation to TrialRecord."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal

from trialsentinel.ingestion.http import SourceHTTPClient
from trialsentinel.ingestion.models import (
    Intervention,
    OutcomeMeasure,
    PartialDate,
    TrialRecord,
    TrialReference,
    stable_hash,
)

SOURCE_NAME = "ctgov"
MAX_PAGE_SIZE = 1000
# Bump whenever parse_study changes what it extracts, so stored records are reprocessed.
PARSER_VERSION = 2


class ClinicalTrialsClient:
    def __init__(self, http: SourceHTTPClient) -> None:
        self._http = http

    async def get_study(self, nct_id: str) -> dict[str, Any]:
        return await self._http.get_json(f"/studies/{nct_id}", params={"format": "json"})

    async def iter_studies(
        self,
        *,
        condition: str | None = None,
        intervention: str | None = None,
        sponsor: str | None = None,
        statuses: Sequence[str] | None = None,
        page_size: int = 100,
        max_studies: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
        if max_studies is not None:
            page_size = min(page_size, max_studies)

        params: dict[str, Any] = {"format": "json", "pageSize": page_size}
        if condition:
            params["query.cond"] = condition
        if intervention:
            params["query.intr"] = intervention
        if sponsor:
            params["query.spons"] = sponsor
        if statuses:
            params["filter.overallStatus"] = ",".join(s.upper() for s in statuses)

        yielded = 0
        seen_tokens: set[str] = set()
        while True:
            page = await self._http.get_json("/studies", params=params)
            for study in page.get("studies", []):
                yield study
                yielded += 1
                if max_studies is not None and yielded >= max_studies:
                    return
            token = page.get("nextPageToken")
            if not token or token in seen_tokens:
                return
            seen_tokens.add(token)
            params["pageToken"] = token


def _date_of(module: dict[str, Any], struct_key: str) -> str | None:
    struct = module.get(struct_key) or {}
    return struct.get("date")


def _outcomes(
    items: list[dict[str, Any]] | None, kind: Literal["primary", "secondary"]
) -> list[OutcomeMeasure]:
    result: list[OutcomeMeasure] = []
    for item in items or []:
        measure = (item.get("measure") or "").strip()
        if not measure:
            continue
        result.append(
            OutcomeMeasure(
                kind=kind,
                position=len(result),
                measure=measure,
                description=item.get("description"),
                time_frame=item.get("timeFrame"),
            )
        )
    return result


def _references(items: list[dict[str, Any]] | None) -> list[TrialReference]:
    refs: list[TrialReference] = []
    seen: set[str] = set()
    for item in items or []:
        pmid = str(item.get("pmid") or "").strip()
        if not pmid.isdigit() or pmid in seen:
            continue
        seen.add(pmid)
        refs.append(TrialReference(pmid=pmid, type=item.get("type"), citation=item.get("citation")))
    return refs


def parse_study(study: dict[str, Any]) -> TrialRecord:
    """Normalise one API v2 study payload. Raises ValueError if it has no NCT ID."""
    protocol = study.get("protocolSection") or {}
    ident = protocol.get("identificationModule") or {}
    nct_id = ident.get("nctId")
    if not nct_id:
        raise ValueError("study is missing protocolSection.identificationModule.nctId")

    status = protocol.get("statusModule") or {}
    sponsor = (protocol.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}
    design = protocol.get("designModule") or {}
    outcomes = protocol.get("outcomesModule") or {}
    arms = protocol.get("armsInterventionsModule") or {}
    primary_completion = status.get("primaryCompletionDateStruct") or {}
    named_interventions = [i for i in arms.get("interventions") or [] if i.get("name")]

    return TrialRecord(
        nct_id=nct_id,
        brief_title=ident.get("briefTitle") or "",
        official_title=ident.get("officialTitle"),
        overall_status=status.get("overallStatus"),
        study_type=design.get("studyType"),
        phases=design.get("phases") or [],
        conditions=(protocol.get("conditionsModule") or {}).get("conditions") or [],
        lead_sponsor_name=sponsor.get("name"),
        lead_sponsor_class=sponsor.get("class"),
        enrollment_count=(design.get("enrollmentInfo") or {}).get("count"),
        start_date=PartialDate.parse(_date_of(status, "startDateStruct")),
        primary_completion_date=PartialDate.parse(primary_completion.get("date")),
        primary_completion_type=primary_completion.get("type"),
        completion_date=PartialDate.parse(_date_of(status, "completionDateStruct")),
        results_first_submit_date=PartialDate.parse(status.get("resultsFirstSubmitDate")),
        results_first_post_date=PartialDate.parse(_date_of(status, "resultsFirstPostDateStruct")),
        last_update_post_date=PartialDate.parse(_date_of(status, "lastUpdatePostDateStruct")),
        has_results=bool(study.get("hasResults", False)),
        brief_summary=(protocol.get("descriptionModule") or {}).get("briefSummary"),
        outcomes=[
            *_outcomes(outcomes.get("primaryOutcomes"), "primary"),
            *_outcomes(outcomes.get("secondaryOutcomes"), "secondary"),
        ],
        interventions=[
            Intervention(type=i.get("type"), name=i["name"].strip(), position=n)
            for n, i in enumerate(named_interventions)
        ],
        references=_references((protocol.get("referencesModule") or {}).get("references")),
        raw_hash=stable_hash({"parser_version": PARSER_VERSION, "study": study}),
    )
