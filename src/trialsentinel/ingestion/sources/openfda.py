"""openFDA FAERS client, drug-name normalisation, and injection-safe query building."""

from __future__ import annotations

import re
from typing import Any

import httpx

from trialsentinel.ingestion.http import SourceHTTPClient

SOURCE_NAME = "openfda_faers"
EVENT_PATH = "/drug/event.json"
DRUG_FIELDS = frozenset({"generic_name", "brand_name"})

_SAFE_TERM = re.compile(r"[a-z0-9][a-z0-9 \-]{1,80}")
_PARENS = re.compile(r"\([^)]*\)|\[[^\]]*\]")
_COMBO_SPLIT = re.compile(r"\s*(?:\+|/|,|;|\band\b|\bplus\b)\s*", re.IGNORECASE)
_DOSE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|ug|µg|g|ml|iu|units?|%)(?=\W|$)", re.IGNORECASE)
_NON_DRUG_MARKERS = ("placebo", "saline", "vehicle", "standard of care", "usual care", "sham")
_FORM_WORDS = frozenset(
    {
        # dosage forms
        "tablet", "tablets", "capsule", "capsules", "injection", "injectable",
        "solution", "suspension", "cream", "gel", "ointment", "patch",
        "inhaler", "inhalation", "spray",
        # release profiles
        "extended", "release", "modified", "delayed", "immediate",
        "er", "xr", "sr", "ir", "dr", "film-coated", "film", "coated",
        # routes
        "oral", "nasal", "subcutaneous", "sc", "iv", "intravenous", "intramuscular", "im",
        # regimen words
        "daily", "once", "twice", "weekly", "dose", "doses", "low", "high", "fixed",
    }
)  # fmt: skip


def normalize_drug_terms(name: str) -> list[str]:
    """'Metformin 500 mg tablets + Sitagliptin' -> ['metformin', 'sitagliptin'].

    Placebo and comparator-care parts are dropped. Output is restricted to a safe
    character set, so it can be embedded in openFDA's search syntax.
    """
    text = _PARENS.sub(" ", name.lower())
    terms: list[str] = []
    for part in _COMBO_SPLIT.split(text):
        if any(marker in part for marker in _NON_DRUG_MARKERS):
            continue
        part = _DOSE.sub(" ", part)
        part = re.sub(r"[^a-z0-9\- ]", " ", part)
        tokens = [
            token
            for token in part.split()
            if token not in _FORM_WORDS and not token.replace("-", "").isdigit()
        ]
        term = " ".join(tokens)
        if len(term) >= 3 and _SAFE_TERM.fullmatch(term) and term not in terms:
            terms.append(term)
    return terms


def drug_search(term: str, field: str) -> str:
    if field not in DRUG_FIELDS:
        raise ValueError(f"unsupported drug field: {field!r}")
    if not _SAFE_TERM.fullmatch(term):
        raise ValueError(f"unsafe drug term: {term!r}")
    return f'patient.drug.openfda.{field}:"{term}"'


def reaction_search(preferred_term: str) -> str:
    escaped = preferred_term.replace("\\", "\\\\").replace('"', '\\"')
    return f'patient.reaction.reactionmeddrapt.exact:"{escaped}"'


class OpenFDAClient:
    def __init__(self, http: SourceHTTPClient) -> None:
        self._http = http

    async def _query(self, params: dict[str, Any]) -> dict[str, Any] | None:
        try:
            return await self._http.get_json(EVENT_PATH, params=params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:  # openFDA's "No matches found"
                return None
            raise

    async def total_reports(self, search: str | None = None) -> tuple[int, str | None]:
        """Number of reports matching `search` (all reports if None), plus data snapshot date."""
        params: dict[str, Any] = {"limit": 1}
        if search:
            params["search"] = search
        data = await self._query(params)
        if not data:
            return 0, None
        meta = data.get("meta") or {}
        return int((meta.get("results") or {}).get("total", 0)), meta.get("last_updated")

    async def top_reactions(self, search: str, *, limit: int) -> list[tuple[str, int]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        data = await self._query(
            {"search": search, "count": "patient.reaction.reactionmeddrapt.exact", "limit": limit}
        )
        if not data:
            return []
        return [(str(r["term"]), int(r["count"])) for r in data.get("results", [])]
