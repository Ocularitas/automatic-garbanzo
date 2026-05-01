"""Public-URL construction for source documents.

Two production patterns supported:

  1. **Caddy-served watch folder** (current POC). When `PUBLIC_BASE_URL` is
     set and the document's `file_path` is under the watch folder, the URL
     points at `<PUBLIC_BASE_URL>/docs/<rel-path>`.

  2. **SharePoint-served** (production target — see `deploy/PRODUCTION.md`).
     When the document carries a `sharepoint_url`, it wins over the
     watch-folder form. Bytes never traverse the gateway; SharePoint serves
     to the user's M365 session directly. Auth + audit + governance stay in
     SharePoint where ABP IT already operates them.

The MCP-server response keys (`document_url`, `<flag>_source_url`) don't
change shape between the two patterns — just the URL the user clicks.

If neither path produces a URL (empty `PUBLIC_BASE_URL`, no SharePoint
locator, or a `file_path` outside the watch folder) the helper returns
`None` and the MCP server omits the URL key from results — better than a
`null` the agent might cite.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from shared.config import get_settings


@dataclass(frozen=True)
class SourceLocator:
    """Where a document lives, addressably.

    `file_path` is the absolute path on the watch-folder host (or wherever
    the ingestion pipeline reads from). `sharepoint_url` is the
    web-viewable SharePoint URL when the document originated there. Both
    optional; populate whichever applies.
    """
    file_path: str | None = None
    sharepoint_url: str | None = None

    @classmethod
    def coerce(cls, value: "SourceLocator | str | None") -> "SourceLocator | None":
        """Accept a locator, a bare file_path string, or None.

        Lets existing call sites that pass `row['file_path']` keep working
        without change while new ones pass a structured locator.
        """
        if value is None:
            return None
        if isinstance(value, SourceLocator):
            return value
        return cls(file_path=str(value))


def build_document_url(
    locator: "SourceLocator | str | None" = None,
    page: int | None = None,
) -> str | None:
    """Build a public URL for a source document, optionally page-anchored.

    Resolution order:

      1. `locator.sharepoint_url` if present — wins because that's the
         tenant-of-record once the SharePoint connector is live. The
         `#page=N` fragment is appended when supplied; whether SharePoint's
         viewer honours it is tenant-configuration-dependent (see
         `deploy/PRODUCTION.md` for the page-anchor caveat).
      2. `<PUBLIC_BASE_URL>/docs/<rel>` if `file_path` is under the watch
         folder and `PUBLIC_BASE_URL` is configured.
      3. None otherwise.

    The `#page=N` fragment works in Chrome / Edge / Firefox / Safari for
    natively-rendered PDFs without a custom viewer.
    """
    loc = SourceLocator.coerce(locator)
    if loc is None:
        return None

    if loc.sharepoint_url:
        url = loc.sharepoint_url
        if page and page > 0:
            url += f"#page={page}"
        return url

    if not loc.file_path:
        return None
    settings = get_settings()
    base = settings.public_base_url
    if not base:
        return None

    try:
        watch = settings.watch_folder.resolve()
        abs_path = Path(loc.file_path).resolve()
        rel = abs_path.relative_to(watch)
    except (ValueError, OSError):
        return None

    encoded = "/".join(quote(part, safe="") for part in rel.parts)
    url = f"{base.rstrip('/')}/docs/{encoded}"
    if page and page > 0:
        url += f"#page={page}"
    return url
