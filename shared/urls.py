"""Public-URL construction for source documents.

The MCP server returns `document_url` (and `document_url_with_page` where a
page is known) in result rows so chat clients can render clickable source
links. URLs point at Caddy's `/docs/` route on the same VM, which file-serves
the watch folder under the bearer-token-gated path.

If `PUBLIC_BASE_URL` is unset (typical for local dev) the helper returns
`None` and the MCP server simply omits the URL key from results.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from shared.config import get_settings


def build_document_url(file_path: str | None, page: int | None = None) -> str | None:
    """Build a public URL for a watch-folder file, optionally page-anchored.

    Returns None if `PUBLIC_BASE_URL` is unset, if the file is outside the
    watch folder, or if the path can't be resolved at all. The caller is
    expected to omit the URL key from the response on None.

    The `#page=N` fragment works in Chrome / Edge / Firefox / Safari for
    natively-rendered PDFs without a custom viewer.
    """
    if not file_path:
        return None
    settings = get_settings()
    base = settings.public_base_url
    if not base:
        return None

    try:
        watch = settings.watch_folder.resolve()
        abs_path = Path(file_path).resolve()
        rel = abs_path.relative_to(watch)
    except (ValueError, OSError):
        return None

    encoded = "/".join(quote(part, safe="") for part in rel.parts)
    url = f"{base.rstrip('/')}/docs/{encoded}"
    if page and page > 0:
        url += f"#page={page}"
    return url
