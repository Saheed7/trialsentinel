from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["health"])


@router.get("/metrics", include_in_schema=False)
def metrics(request: Request) -> Response:
    """Prometheus scrape endpoint. Holds no trial data or secrets; restrict it at the
    network level when deployed."""
    return Response(
        generate_latest(request.app.state.metrics.registry), media_type=CONTENT_TYPE_LATEST
    )
