from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from trialsentinel.agents.service import DEFAULT_AS_OF
from trialsentinel.api.schemas import Assessment, FindingsPage
from trialsentinel.api.security import ApiKey

router = APIRouter(prefix="/v1", tags=["trials"])


@router.get("/findings", response_model=FindingsPage, summary="Rule-engine findings")
async def list_findings(
    request: Request,
    _key: ApiKey,
    as_of: date = DEFAULT_AS_OF,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FindingsPage:
    items = await request.app.state.store.findings(as_of)
    return FindingsPage(as_of=as_of, total=len(items), items=items[offset : offset + limit])


@router.get(
    "/trials/{nct_id}/assessment",
    response_model=Assessment,
    summary="The FDAAA 801 rule assessment for one trial",
)
async def get_assessment(
    request: Request,
    _key: ApiKey,
    nct_id: Annotated[str, Path(pattern=r"^NCT\d{8}$")],
    as_of: date = DEFAULT_AS_OF,
) -> Assessment:
    assessment = await request.app.state.store.assessment(nct_id, as_of)
    if assessment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no assessment for {nct_id} as of {as_of}")
    return assessment
