from collections.abc import Callable

import httpx
import pytest
from tenacity import wait_none

from trialsentinel.ingestion.http import SourceHTTPClient
from trialsentinel.ingestion.sources.clinicaltrials import ClinicalTrialsClient

pytestmark = pytest.mark.anyio

Handler = Callable[[httpx.Request], httpx.Response]


def _http(handler: Handler, max_retries: int = 3) -> SourceHTTPClient:
    return SourceHTTPClient(
        "https://ct.test/api/v2",
        rate_per_sec=1000,
        timeout_s=5,
        max_retries=max_retries,
        user_agent="trialsentinel-tests",
        transport=httpx.MockTransport(handler),
        retry_wait=wait_none(),
    )


async def test_paginates_until_no_token() -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        if "pageToken" not in request.url.params:
            return httpx.Response(
                200, json={"studies": [{"id": 1}, {"id": 2}], "nextPageToken": "p2"}
            )
        return httpx.Response(200, json={"studies": [{"id": 3}]})

    async with _http(handler) as http:
        client = ClinicalTrialsClient(http)
        studies = [s async for s in client.iter_studies(condition="asthma", statuses=["completed"])]

    assert [s["id"] for s in studies] == [1, 2, 3]
    assert calls[0]["query.cond"] == "asthma"
    assert calls[0]["filter.overallStatus"] == "COMPLETED"
    assert calls[1]["pageToken"] == "p2"


async def test_stops_at_max_studies() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"studies": [{"id": i} for i in range(5)], "nextPageToken": "again"}
        )

    async with _http(handler) as http:
        studies = [s async for s in ClinicalTrialsClient(http).iter_studies(max_studies=3)]

    assert len(studies) == 3


async def test_repeating_cursor_does_not_loop_forever() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"studies": [{"id": 1}], "nextPageToken": "same"})

    async with _http(handler) as http:
        studies = [s async for s in ClinicalTrialsClient(http).iter_studies()]

    assert len(studies) == 2  # first page, one follow-up, then the repeat is detected


async def test_retries_transient_errors() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"studies": []})

    async with _http(handler) as http:
        assert [s async for s in ClinicalTrialsClient(http).iter_studies()] == []
    assert attempts["n"] == 2


async def test_does_not_retry_client_errors() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(400, json={"error": "bad query"})

    async with _http(handler) as http:
        with pytest.raises(httpx.HTTPStatusError):
            _ = [s async for s in ClinicalTrialsClient(http).iter_studies()]
    assert attempts["n"] == 1
