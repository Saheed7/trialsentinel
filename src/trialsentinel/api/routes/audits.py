from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status

from trialsentinel.agents.service import AuditOptions
from trialsentinel.api.jobs import AuditJob, QueueFull
from trialsentinel.api.schemas import (
    AuditRequest,
    AuditResult,
    AuditStatus,
    AuditSummary,
    NctId,
)
from trialsentinel.api.security import ApiKey
from trialsentinel.core.logging import get_logger

router = APIRouter(prefix="/v1/audits", tags=["audits"])
history = APIRouter(prefix="/v1/trials", tags=["audits"])
log = get_logger(__name__)


def _status(job: AuditJob) -> AuditStatus:
    result = None
    if job.result is not None:
        r = job.result
        result = AuditResult(
            verdict=r["verdict"],
            status=r["status"],
            claims=r["claims"],
            rejected=r["rejected"],
            evidence=r["evidence"],
            audit=[{k: str(v) for k, v in row.items()} for row in r["audit_rows"]],
            revisions=r["revisions"],
            seconds=r["seconds"],
            verifier_version=r["verifier_version"],
            prompt_version=r["prompt_version"],
            judge=r["judge"],
        )
    return AuditStatus(
        audit_id=job.audit_id,
        nct_id=job.nct_id,
        as_of=job.as_of,
        status=job.status,
        submitted_at=job.submitted_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        result=result,
        error=job.error,
    )


@router.post(
    "",
    response_model=AuditStatus,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an audit: explain the rule verdict with verified claims",
)
async def create_audit(
    body: AuditRequest, request: Request, response: Response, key: ApiKey
) -> AuditStatus:
    case = await request.app.state.store.case(body.nct_id, body.as_of)
    if case is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"no assessment for {body.nct_id} as of {body.as_of}"
        )
    options = AuditOptions(
        prompt_version=body.prompt_version,
        judge=body.judge,
        judge_prompt_version=body.judge_prompt_version,
    )
    try:
        job = request.app.state.jobs.submit(case, options, as_of=body.as_of, key_fingerprint=key)
    except QueueFull:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many audits waiting; retry later",
            headers={"Retry-After": "60"},
        ) from None
    log.info("audit_submitted", audit_id=job.audit_id, nct_id=job.nct_id, key=key)
    response.headers["Location"] = f"/v1/audits/{job.audit_id}"
    return _status(job)


@router.get("/{audit_id}", response_model=AuditStatus, summary="Audit status and result")
async def get_audit(
    request: Request,
    _key: ApiKey,
    audit_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")],
) -> AuditStatus:
    # Recent and running audits are in memory; older ones (and those from before a restart)
    # come from the database.
    job = request.app.state.jobs.get(audit_id) or await request.app.state.store.load_audit(audit_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown audit id")
    return _status(job)


@history.get(
    "/{nct_id}/audits",
    response_model=list[AuditSummary],
    summary="Audit history for one trial, newest first",
)
async def list_trial_audits(
    request: Request,
    _key: ApiKey,
    nct_id: Annotated[NctId, Path()],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[AuditSummary]:
    jobs = await request.app.state.store.list_audits(nct_id, limit)
    return [
        AuditSummary(
            audit_id=j.audit_id,
            nct_id=j.nct_id,
            as_of=j.as_of,
            status=j.status,
            submitted_at=j.submitted_at,
            finished_at=j.finished_at,
            outcome=(j.result or {}).get("status"),
            verified_claims=len((j.result or {}).get("claims", [])),
        )
        for j in jobs
    ]
