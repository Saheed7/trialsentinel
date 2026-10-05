"""PubMed via NCBI E-utilities: trial-linked search and hardened XML parsing."""

from __future__ import annotations

import re
from collections.abc import Sequence

# Only serialising/typing comes from the stdlib module; all parsing goes through defusedxml.
from xml.etree.ElementTree import Element, tostring  # noqa: S405

from defusedxml.ElementTree import fromstring

from trialsentinel.ingestion.http import SourceHTTPClient
from trialsentinel.ingestion.models import (
    LinkSource,
    PartialDate,
    PublicationRecord,
    stable_bytes_hash,
)

SOURCE_NAME = "pubmed"
EFETCH_BATCH_SIZE = 200
NCT_ID_PATTERN = re.compile(r"^NCT\d{8}$")
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}


class PubMedError(RuntimeError):
    """E-utilities returned a well-formed response that reports an error."""


class PubMedClient:
    def __init__(self, http: SourceHTTPClient) -> None:
        self._http = http

    async def search(self, term: str, *, retmax: int = 100) -> list[str]:
        data = await self._http.get_json(
            "/esearch.fcgi",
            params={"db": "pubmed", "term": term, "retmode": "json", "retmax": retmax},
        )
        result = data.get("esearchresult") or {}
        if "ERROR" in result:
            raise PubMedError(str(result["ERROR"]))
        return [str(pmid) for pmid in result.get("idlist", [])]

    async def search_trial(self, nct_id: str) -> dict[LinkSource, list[str]]:
        """Search the two PubMed fields that can mention a trial, keeping them separate."""
        if not NCT_ID_PATTERN.fullmatch(nct_id):
            raise ValueError(f"not a valid NCT ID: {nct_id!r}")  # also blocks query injection
        return {
            LinkSource.PUBMED_SECONDARY_ID: await self.search(f"{nct_id}[si]"),
            LinkSource.PUBMED_TEXT: await self.search(f"{nct_id}[tiab]"),
        }

    async def fetch_xml(self, pmids: Sequence[str]) -> str:
        if len(pmids) > EFETCH_BATCH_SIZE:
            raise ValueError(f"at most {EFETCH_BATCH_SIZE} PMIDs per request")
        return await self._http.get_text(
            "/efetch.fcgi", params={"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
        )


def _text(element: Element | None) -> str | None:
    if element is None:
        return None
    text = " ".join("".join(element.itertext()).split())
    return text or None


def _date_from(element: Element | None) -> PartialDate | None:
    if element is None:
        return None
    year = _text(element.find("Year"))
    if not year:
        match = re.match(r"(\d{4})", _text(element.find("MedlineDate")) or "")
        return PartialDate.parse(match.group(1)) if match else None
    parts = [year]
    month = _text(element.find("Month"))
    if month:
        number = int(month) if month.isdigit() else _MONTHS.get(month[:3].lower())
        if number:
            parts.append(f"{number:02d}")
            day = _text(element.find("Day"))
            if day and day.isdigit():
                parts.append(f"{int(day):02d}")
    return PartialDate.parse("-".join(parts))


def _registry_ids(article: Element) -> list[str]:
    ids: set[str] = set()
    for bank in article.findall("DataBankList/DataBank"):
        if (_text(bank.find("DataBankName")) or "").lower() != "clinicaltrials.gov":
            continue
        for accession in bank.findall("AccessionNumberList/AccessionNumber"):
            value = (_text(accession) or "").upper()
            if NCT_ID_PATTERN.fullmatch(value):
                ids.add(value)
    return sorted(ids)


def parse_pubmed_xml(xml_text: str) -> list[PublicationRecord]:
    """Parse an efetch response. defusedxml rejects entity-expansion and XXE payloads."""
    root = fromstring(xml_text)
    records: list[PublicationRecord] = []
    for entry in root.iter("PubmedArticle"):
        citation = entry.find("MedlineCitation")
        if citation is None:
            continue
        pmid = _text(citation.find("PMID"))
        article = citation.find("Article")
        if not pmid or article is None:
            continue

        abstract_parts: list[str] = []
        for node in article.findall("Abstract/AbstractText"):
            text = _text(node)
            if text:
                label = node.get("Label")
                abstract_parts.append(f"{label}: {text}" if label else text)

        doi = next(
            (
                _text(node)
                for node in entry.findall("PubmedData/ArticleIdList/ArticleId")
                if node.get("IdType") == "doi"
            ),
            None,
        )
        types = [
            t
            for t in (_text(n) for n in article.findall("PublicationTypeList/PublicationType"))
            if t
        ]

        records.append(
            PublicationRecord(
                pmid=pmid,
                title=_text(article.find("ArticleTitle")) or "",
                abstract="\n".join(abstract_parts) or None,
                journal=_text(article.find("Journal/Title")),
                # Electronic date first: it is the earliest public date, which matters
                # for time-to-publication analysis.
                pub_date=_date_from(article.find("ArticleDate"))
                or _date_from(article.find("Journal/JournalIssue/PubDate")),
                doi=doi,
                publication_types=types,
                registry_ids=_registry_ids(article),
                raw_hash=stable_bytes_hash(tostring(entry)),
            )
        )
    return records
