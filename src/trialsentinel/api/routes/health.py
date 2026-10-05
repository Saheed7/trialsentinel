from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from trialsentinel.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str


@router.get("/healthz", response_model=HealthResponse, summary="Liveness probe")
def liveness(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.service_name,
        version=settings.version,
        environment=settings.env,
    )
