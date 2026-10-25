"""Sentence-aware chunking with overlap, and deterministic chunk IDs for idempotent indexing."""

import re
import uuid

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
# Fixed namespace: the same (type, id, index) always maps to the same point ID.
CHUNK_NAMESPACE = uuid.UUID("6f1c7d3e-2b4a-4c8e-9a51-3d2f7e8b9c10")


def split_sentences(text: str) -> list[str]:
    collapsed = " ".join(text.split())
    return [s.strip() for s in _SENTENCE_END.split(collapsed) if s.strip()]


def chunk_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    """Pack whole sentences into chunks of at most `max_chars`, repeating a word-aligned
    tail of the previous chunk so that context spanning a boundary is not lost."""
    if max_chars <= 0 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("require max_chars > 0 and 0 <= overlap_chars < max_chars")

    chunks: list[str] = []
    current = ""
    for sentence in split_sentences(text):
        pieces = (
            [sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars)]
            if len(sentence) > max_chars
            else [sentence]
        )
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
            tail = current[-overlap_chars:] if overlap_chars and current else ""
            if " " in tail:
                tail = tail[tail.index(" ") + 1 :]  # start the overlap on a word boundary
            current = f"{tail} {piece}".strip()
            if len(current) > max_chars:
                current = piece
    if current:
        chunks.append(current)
    return chunks


def chunk_id(source_type: str, source_id: str, index: int) -> str:
    return str(uuid.uuid5(CHUNK_NAMESPACE, f"{source_type}:{source_id}:{index}"))
