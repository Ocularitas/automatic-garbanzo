"""Chunker behaviour: page mapping, overlap, empty input."""
from __future__ import annotations

from ingestion.chunker import OVERLAP_CHARS, TARGET_CHUNK_CHARS, chunk_text
from ingestion.parser import ParsedPdf


def _parsed(pages: list[str]) -> ParsedPdf:
    parts = []
    offsets = []
    cursor = 0
    for p in pages:
        offsets.append(cursor)
        parts.append(p)
        cursor += len(p) + 2
    return ParsedPdf(
        full_text="\n\n".join(parts),
        page_char_offsets=offsets,
        num_pages=len(pages),
    )


def test_empty_input_yields_no_chunks() -> None:
    assert chunk_text(_parsed([""])) == []


def test_single_short_doc_yields_one_chunk() -> None:
    text = "This is a short clause." * 5
    chunks = chunk_text(_parsed([text]))
    assert len(chunks) == 1
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 1


def test_long_doc_splits_with_overlap_and_pages() -> None:
    page1 = ("Section one. " * 400)  # ~5200 chars
    page2 = ("Section two. " * 400)
    parsed = _parsed([page1, page2])
    chunks = chunk_text(parsed)
    assert len(chunks) >= 2
    # All chunks within bounds.
    for c in chunks:
        assert 0 <= c.char_start < c.char_end <= len(parsed.full_text)
        assert c.page_start in (1, 2)
        assert c.page_end in (1, 2)
        assert c.page_end >= c.page_start
    # Adjacent chunks should overlap by approximately OVERLAP_CHARS.
    a, b = chunks[0], chunks[1]
    overlap = a.char_end - b.char_start
    assert -10 <= overlap <= TARGET_CHUNK_CHARS, (
        f"overlap {overlap} outside expected window (target overlap {OVERLAP_CHARS})"
    )


def test_page_for_char_boundaries() -> None:
    parsed = _parsed(["aaa", "bbb", "ccc"])
    # offsets: [0, 5, 10] with "\n\n" between.
    assert parsed.page_for_char(0) == 1
    assert parsed.page_for_char(4) == 1
    assert parsed.page_for_char(5) == 2
    assert parsed.page_for_char(10) == 3
