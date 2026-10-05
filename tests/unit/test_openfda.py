from collections.abc import Callable

import httpx
import pytest
from tenacity import wait_none

from trialsentinel.ingestion.http import SourceHTTPClient
from trialsentinel.ingestion.sources.openfda import (
    OpenFDAClient,
    drug_search,
    normalize_drug_terms,
    reaction_search,
)

Handler = Callable[[httpx.Request], httpx.Response]


def _http(handler: Handler) -> SourceHTTPClient:
    return SourceHTTPClient(
        "https://openfda.test",
        rate_per_sec=1000,
        timeout_s=5,
        max_retries=3,
        user_agent="trialsentinel-tests",
        transport=httpx.MockTransport(handler),
        retry_wait=wait_none(),
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Metformin 500 mg tablets", ["metformin"]),
        ("Dapagliflozin 10mg", ["dapagliflozin"]),
        ("Insulin glargine (Lantus)", ["insulin glargine"]),
        ("Metformin + Sitagliptin", ["metformin", "sitagliptin"]),
        ("Sitagliptin and placebo", ["sitagliptin"]),
        ("LY3298176", ["ly3298176"]),
        ("Matching placebo tablet", []),
        ("Saline", []),
        ("Standard of care", []),
    ],
)
def test_normalize_drug_terms(name: str, expected: list[str]) -> None:
    assert normalize_drug_terms(name) == expected


def test_drug_search_blocks_injection_and_unknown_fields() -> None:
    assert (
        drug_search("metformin", "generic_name") == 'patient.drug.openfda.generic_name:"metformin"'
    )
    with pytest.raises(ValueError):
        drug_search('metformin" OR *:*', "generic_name")
    with pytest.raises(ValueError):
        drug_search("metformin", "manufacturer_name")


def test_reaction_search_escapes_quotes() -> None:
    assert reaction_search('DRUG "X"') == 'patient.reaction.reactionmeddrapt.exact:"DRUG \\"X\\""'


@pytest.mark.anyio
async def test_404_no_matches_means_zero_reports() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    async with _http(handler) as http:
        total = await OpenFDAClient(http).total_reports(drug_search("zzzdrug", "generic_name"))
    assert total == (0, None)


@pytest.mark.anyio
async def test_total_and_count_parsing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "count" in request.url.params:
            return httpx.Response(
                200,
                json={
                    "results": [{"term": "NAUSEA", "count": 50}, {"term": "HEADACHE", "count": 20}]
                },
            )
        return httpx.Response(
            200,
            json={
                "meta": {"last_updated": "2026-09-30", "results": {"total": 1234}},
                "results": [{}],
            },
        )

    async with _http(handler) as http:
        client = OpenFDAClient(http)
        assert await client.total_reports() == (1234, "2026-09-30")
        reactions = await client.top_reactions(drug_search("metformin", "generic_name"), limit=2)
    assert reactions == [("NAUSEA", 50), ("HEADACHE", 20)]


@pytest.mark.anyio
async def test_server_errors_are_not_mistaken_for_no_matches() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": "BAD_REQUEST"}})

    async with _http(handler) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await OpenFDAClient(http).total_reports()
