"""PDF text extraction for chunking. Anthropic still parses the PDF natively
for extraction; this layer only feeds the local chunker.

If a PDF is image-only (scanned), pypdf returns empty text and the chunker
will produce zero chunks. Extraction can still succeed — Claude reads the
PDF natively — but vector search won't help for that document. Logged.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass
class ParsedPdf:
    full_text: str
    # `page_char_offsets[i]` is the char index in `full_text` where page i+1 begins.
    page_char_offsets: list[int]
    num_pages: int

    def page_for_char(self, char_index: int) -> int:
        """1-indexed page that contains the given char position."""
        # Binary search would be tidier; corpora are small enough that linear is fine.
        page = 1
        for i, start in enumerate(self.page_char_offsets, start=1):
            if char_index >= start:
                page = i
            else:
                break
        return page


def parse_pdf(path: Path) -> ParsedPdf:
    reader = PdfReader(str(path))
    parts: list[str] = []
    offsets: list[int] = []
    cursor = 0
    for page in reader.pages:
        offsets.append(cursor)
        text = page.extract_text() or ""
        parts.append(text)
        cursor += len(text) + 2  # +2 for the "\n\n" we insert between pages
    return ParsedPdf(
        full_text="\n\n".join(parts),
        page_char_offsets=offsets,
        num_pages=len(reader.pages),
    )
