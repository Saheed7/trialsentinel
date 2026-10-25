import pytest

from trialsentinel.retrieval.chunking import chunk_id, chunk_text, split_sentences
from trialsentinel.retrieval.documents import publication_document, trial_document
from trialsentinel.retrieval.evaluate import score_ranking, unique_sources
from trialsentinel.retrieval.search import build_filter

# Every token is unique, so shared tokens between chunks can only come from overlap.
TEXT = " ".join(f"Alpha{i} beta{i} gamma{i} delta{i}." for i in range(60))


def test_split_sentences() -> None:
    assert split_sentences("One here. Two there!  Three?") == ["One here.", "Two there!", "Three?"]


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("A short abstract.", max_chars=200, overlap_chars=50) == ["A short abstract."]


def test_empty_text_has_no_chunks() -> None:
    assert chunk_text("   ", max_chars=200, overlap_chars=50) == []


def test_chunks_respect_max_size() -> None:
    chunks = chunk_text(TEXT, max_chars=200, overlap_chars=50)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)


def test_consecutive_chunks_overlap() -> None:
    chunks = chunk_text(TEXT, max_chars=200, overlap_chars=50)
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current.split()[0] in previous.split()


def test_overlong_sentence_is_hard_split() -> None:
    chunks = chunk_text("x" * 450, max_chars=200, overlap_chars=0)
    assert [len(c) for c in chunks] == [200, 200, 50]


def test_invalid_parameters_are_rejected() -> None:
    with pytest.raises(ValueError):
        chunk_text("text", max_chars=100, overlap_chars=100)


def test_chunk_ids_are_deterministic_and_distinct() -> None:
    assert chunk_id("trial", "NCT01234567", 0) == chunk_id("trial", "NCT01234567", 0)
    assert chunk_id("trial", "NCT01234567", 0) != chunk_id("trial", "NCT01234567", 1)
    assert chunk_id("trial", "123", 0) != chunk_id("publication", "123", 0)


def test_trial_document_combines_summary_and_outcomes() -> None:
    doc = trial_document("NCT01234567", "Drug X in asthma", "Tests drug X.", ["FEV1 at 12 weeks"])
    assert doc is not None
    assert doc.text == "Tests drug X. Primary outcomes: FEV1 at 12 weeks."
    assert doc.nct_ids == ("NCT01234567",)


def test_empty_documents_are_skipped() -> None:
    assert trial_document("NCT01234567", "Title", None, []) is None
    assert publication_document("1", "", None, [], None) is None


def test_publication_falls_back_to_title_and_sorts_links() -> None:
    doc = publication_document("1", "A title", None, ["NCT2", "NCT1"], "2020-01-15")
    assert doc is not None
    assert doc.text == "A title"
    assert doc.nct_ids == ("NCT1", "NCT2")


def test_build_filter() -> None:
    assert build_filter() is None
    flt = build_filter(doc_type="publication", nct_id="NCT01234567")
    assert flt is not None and len(flt.must) == 2


def test_score_ranking() -> None:
    relevant = frozenset({"p2"})
    assert score_ranking(["p1", "p2", "p3"], relevant, k=1) == (0.0, 0.5)
    assert score_ranking(["p1", "p2", "p3"], relevant, k=5) == (1.0, 0.5)
    assert score_ranking(["p1", "p3"], relevant, k=5) == (0.0, 0.0)


def test_unique_sources_keeps_first_occurrence_order() -> None:
    assert unique_sources(["p1", "p1", "p2", "p1", "p3"]) == ["p1", "p2", "p3"]
