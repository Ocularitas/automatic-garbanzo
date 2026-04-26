"""Text chunking with source linkback (page + char offsets).

POC strategy: try to split on blank-line / clause-ish boundaries within a target
size window; fall back to fixed-size on long paragraphs. Overlap by ~100 tokens
(~400 chars) between adjacent chunks.

Token counts are approximated by char/4. Good enough for POC chunk sizing.
"""
from __future__ import annotations

from dataclasses import dataclass

from ingestion.parser import ParsedPdf

TARGET_CHUNK_CHARS = 1500   # ~375 tokens — clause-sized for typical contracts
OVERLAP_CHARS = 200         # ~50 tokens
MIN_CHUNK_CHARS = 200


@dataclass
class Chunk:
    index: int
    text: str
    char_start: int
    char_end: int
    page_start: int
    page_end: int


def chunk_text(parsed: ParsedPdf) -> list[Chunk]:
    text = parsed.full_text
    if not text.strip():
        return []

    boundaries = _candidate_boundaries(text)
    chunks: list[Chunk] = []
    cursor = 0
    idx = 0
    n = len(text)

    while cursor < n:
        end_target = min(cursor + TARGET_CHUNK_CHARS, n)
        # Snap to the nearest paragraph boundary at or after cursor + MIN.
        snap = _snap_forward(boundaries, end_target)
        chunk_end = snap if snap is not None else end_target
        chunk_text_str = text[cursor:chunk_end].strip()
        if len(chunk_text_str) >= MIN_CHUNK_CHARS or chunk_end == n:
            chunks.append(Chunk(
                index=idx,
                text=chunk_text_str,
                char_start=cursor,
                char_end=chunk_end,
                page_start=parsed.page_for_char(cursor),
                page_end=parsed.page_for_char(max(cursor, chunk_end - 1)),
            ))
            idx += 1
        if chunk_end >= n:
            break
        cursor = max(cursor + MIN_CHUNK_CHARS, chunk_end - OVERLAP_CHARS)

    return chunks


def _candidate_boundaries(text: str) -> list[int]:
    """Indices where a chunk could cleanly end (after a blank line)."""
    out = []
    i = 0
    while True:
        j = text.find("\n\n", i)
        if j == -1:
            break
        out.append(j + 2)
        i = j + 2
    return out


def _snap_forward(boundaries: list[int], target: int) -> int | None:
    """Smallest boundary >= target."""
    for b in boundaries:
        if b >= target:
            return b
    return None
