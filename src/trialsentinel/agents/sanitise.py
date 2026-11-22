"""Neutralises untrusted excerpt text before an LLM or the verifier sees it.

Excerpts are free text written by sponsors and authors. Text that imitates TrialSentinel's
own evidence format (evidence IDs, kind markers, publication-link lines) is defused, markup
is stripped, and the excerpt is labelled as untrusted."""

import re

UNTRUSTED_LABEL = "[untrusted quoted text]"

_HTML_TAG = re.compile(r"<[^>]{0,200}>")
_SPOOFED_ID = re.compile(r"\[\s*E\d+\s*\]", re.IGNORECASE)
_SPOOFED_KIND = re.compile(r"\(\s*(rule|registry|publication|excerpt)\s*\)", re.IGNORECASE)
_SPOOFED_LINK = re.compile(r"\bPubMed\s+(\d+)\s+is\s+linked\s+to\b", re.IGNORECASE)


def sanitise_excerpt(text: str) -> str:
    """Strips markup and defuses imitations of TrialSentinel's evidence format."""
    text = _HTML_TAG.sub(" ", text)
    text = _SPOOFED_ID.sub("[quoted id]", text)
    text = _SPOOFED_KIND.sub(r"(quoted \1)", text)
    text = _SPOOFED_LINK.sub(r"PubMed \1 is said by this quoted text to be linked to", text)
    text = re.sub(r"\s+", " ", text).strip()
    return f"{UNTRUSTED_LABEL} {text}"
