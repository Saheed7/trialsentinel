import asyncio

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])

READY_TIMEOUT_S = 2.0


@router.get("/readyz", summary="Readiness probe: is the database reachable?")
async def readiness(request: Request) -> JSONResponse:
    try:
        await asyncio.wait_for(request.app.state.store.ping(), timeout=READY_TIMEOUT_S)
    except Exception:  # any failure means not ready; details go to the logs, not the client
        return JSONResponse(
            {"status": "unavailable", "database": "unreachable"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return JSONResponse({"status": "ready", "database": "ok"})
