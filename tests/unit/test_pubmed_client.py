from collections.abc import Callable

import httpx
import pytest
from tenacity import wait_none

from trialsentinel.ingestion.http import SourceHTTPClient
from trialsentinel.ingestion.models import LinkSource
from trialsentinel.ingestion.sources.pubmed import PubMedClient, PubMedError

pytestmark = pytest.mark.anyio

Handler = Callable[[httpx.Request], httpx.Response]


def _http(handler: Handler) -> SourceHTTPClient:
    return SourceHTTPClient(
        "https://eutils.test/entrez/eutils",
        rate_per_sec=1000,
        timeout_s=5,
        max_retries=3,
        user_agent="trialsentinel-tests",
        default_params={"tool": "trialsentinel", "api_key": "test-key"},
        transport=httpx.MockTransport(handler),
        retry_wait=wait_none(),
    )


async def test_search_trial_queries_both_fields_with_ncbi_params() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"esearchresult": {"idlist": ["111", "222"]}})

    async with _http(handler) as http:
        found = await PubMedClient(http).search_trial("NCT01234567")

    assert [p["term"] for p in seen] == ["NCT01234567[si]", "NCT01234567[tiab]"]
    assert all(p["tool"] == "trialsentinel" and p["api_key"] == "test-key" for p in seen)
    assert found[LinkSource.PUBMED_SECONDARY_ID] == ["111", "222"]


async def test_invalid_nct_id_is_rejected_before_any_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be sent")

    async with _http(handler) as http:
        with pytest.raises(ValueError, match="NCT ID"):
            await PubMedClient(http).search_trial("NCT123 OR cancer[mh]")


async def test_esearch_error_is_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"esearchresult": {"ERROR": "Invalid query"}})

    async with _http(handler) as http:
        with pytest.raises(PubMedError, match="Invalid query"):
            await PubMedClient(http).search("x")


async def test_fetch_xml_rejects_oversized_batches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be sent")

    async with _http(handler) as http:
        with pytest.raises(ValueError, match="at most"):
            await PubMedClient(http).fetch_xml([str(i) for i in range(201)])
