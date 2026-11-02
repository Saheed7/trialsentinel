"""Request and response models: the API's public contract (shown in /docs)."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from trialsentinel.agents.service import DEFAULT_AS_OF

NctId = Annotated[str, Field(pattern=r"^NCT\d{8}$", examples=["NCT03317028"])]
AuditState = Literal["queued", "running", "succeeded", "failed"]


class Finding(BaseModel):
    nct_id: str
    finding: str
    reporting_status: str | None
    days_overdue: int | None
    days_late: int | None


class FindingsPage(BaseModel):
    as_of: date
    total: int
    items: list[Finding]


class Assessment(BaseModel):
    nct_id: str
    as_of: date
    rule_version: str
    applicability: str | None
    applicability_confidence: str | None
    reporting_status: str | None
    standard_deadline: date | None
    effective_deadline: date | None
    days_overdue: int | None
    days_late: int | None
    finding: str | None
    basis: list[str]


class AuditRequest(BaseModel):
    nct_id: NctId
    as_of: date = DEFAULT_AS_OF
    prompt_version: Literal[1, 2] = 2
    judge: bool = Field(default=False, description="Also run the LLM judge inside the loop")
    judge_prompt_version: Literal[1, 2] = 2


class VerifiedClaim(BaseModel):
    text: str
    evidence_ids: list[str]


class RejectedClaim(BaseModel):
    claim: str
    reason: str
    round: str


class EvidenceItem(BaseModel):
    id: str
    kind: str
    text: str


class AuditResult(BaseModel):
    verdict: str
    status: Literal["verified", "needs_review"]
    claims: list[VerifiedClaim]
    rejected: list[RejectedClaim]
    evidence: list[EvidenceItem]
    audit: list[dict[str, str]] = Field(description="Every layer's decision on every claim")
    revisions: int
    seconds: float
    verifier_version: int
    prompt_version: int
    judge: bool


class AuditStatus(BaseModel):
    audit_id: str
    nct_id: str
    as_of: date
    status: AuditState
    submitted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: AuditResult | None = None
    error: str | None = None
