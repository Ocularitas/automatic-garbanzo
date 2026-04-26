"""Query MCP server. Streamable-HTTP transport, FastMCP."""
from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP

from ingestion.embedder import embed_query
from mcp_servers.query import store
from shared.config import get_settings
from shared.identity import current_identity

log = logging.getLogger(__name__)

mcp = FastMCP("contract-intelligence-query")


@mcp.tool()
def vector_search(
    query: str,
    top_k: int = 8,
    folder_prefix: str | None = None,
    rule_id: str | None = None,
) -> dict[str, Any]:
    """Semantic search across contract chunks.

    Use this for single-document Q&A and discovery questions, e.g. "what does
    our corpus say about indemnity caps?" or "find the renewal terms in the
    SAP contract". Each hit includes the document id, page number, file path,
    and a text snippet so the answer can be cited.

    Args:
        query: Natural language search query.
        top_k: Number of results (default 8, max 50).
        folder_prefix: Optional substring filter on the source path,
            e.g. "saas" or "leases".
        rule_id: Optional restriction to a single rule, e.g. "saas_contract".
    """
    top_k = max(1, min(top_k, 50))
    identity = current_identity()
    embedding = embed_query(query)
    hits = store.vector_search(
        query_embedding=embedding,
        top_k=top_k,
        folder_prefix=folder_prefix,
        rule_id=rule_id,
        group_id=identity.group_id,
    )
    return {
        "hits": [
            {
                "document_id": str(h.document_id),
                "chunk_id": str(h.chunk_id),
                "chunk_index": h.chunk_index,
                "page_start": h.page_start,
                "page_end": h.page_end,
                "score": round(h.score, 4),
                "rule_id": h.rule_id,
                "file_path": h.file_path,
                "snippet": h.text[:600],
            }
            for h in hits
        ]
    }


@mcp.tool()
def query_contracts_structured(
    filters: dict[str, Any] | None = None,
    select: list[str] | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Run a structured query against extracted contract fields.

    Use this for aggregations and filtered lists, e.g. "all contracts expiring
    in Q2 2026", "all SaaS contracts with payment terms over net 60", "every
    lease without a break clause". Returns rows with source links and supports
    cursor pagination.

    Filter shape:
      - Equality: {"rule_id": "saas_contract"}
      - Operators: {"expiry_date": {"lte": "2026-06-30"}}
      - Lists: {"currency": {"in": ["GBP", "USD"]}}
      - Clause flags: {"clauses.has_dr_clause": false}

    Allowed fields: rule_id, rule_version, effective_date, expiry_date,
    currency, annual_value, file_path, plus clauses.<flag_name>.
    Allowed operators: eq, ne, lt, lte, gt, gte, in, like, is_null.

    Args:
        filters: Filter dict as above.
        select: Optional list of fields to return; otherwise all are returned.
        limit: Page size, default 50, max 500.
        cursor: Cursor from a previous response's `next_cursor`.
    """
    limit = max(1, min(limit, 500))
    identity = current_identity()
    rows, next_cursor = store.query_contracts_structured(
        filters=filters or {},
        select_fields=select,
        limit=limit,
        cursor=cursor,
        group_id=identity.group_id,
    )
    return {
        "rows": [_jsonable(r) for r in rows],
        "next_cursor": next_cursor,
        "has_more": next_cursor is not None,
    }


@mcp.tool()
def get_contract(contract_id: str) -> dict[str, Any]:
    """Fetch the full extracted record for one contract by id.

    Returns every extracted field, every clause flag, the per-field source
    links (page + quote), and the originating file path. Use this after
    `vector_search` or `query_contracts_structured` returns a contract id
    you want to inspect in detail.
    """
    from uuid import UUID
    identity = current_identity()
    row = store.get_contract(UUID(contract_id), group_id=identity.group_id)
    if not row:
        return {"error": f"Contract {contract_id} not found"}
    return _jsonable(row)


@mcp.tool()
def list_contracts(
    folder_prefix: str | None = None,
    rule_id: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Browse the contract corpus.

    Lightweight; for "what's in here?" questions, not analysis. Returns the
    most recently ingested contracts first. Use `query_contracts_structured`
    for filtered analysis or `vector_search` for content-based discovery.
    """
    limit = max(1, min(limit, 200))
    identity = current_identity()
    rows = store.list_contracts(
        folder_prefix=folder_prefix,
        rule_id=rule_id,
        limit=limit,
        group_id=identity.group_id,
    )
    return {"contracts": [_jsonable(r) for r in rows]}


@mcp.tool()
def find_clause_gaps(
    clause_flag: str,
    rule_id: str | None = None,
    folder_prefix: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Find contracts where a named clause is absent (presence flag is false).

    Convenience wrapper for the negative-space pattern: which contracts are
    missing X? Examples: clause_flag="has_dr_clause" returns SaaS contracts
    without disaster recovery commitments; "has_break_clause" with
    rule_id="lease" returns leases without a tenant break right.

    Args:
        clause_flag: The clause flag to test, e.g. "has_dr_clause".
        rule_id: Optional restriction to one rule.
        folder_prefix: Optional substring filter on file path.
        limit: Max results, default 100.
    """
    limit = max(1, min(limit, 500))
    identity = current_identity()
    rows = store.find_clause_gaps(
        clause_flag=clause_flag,
        rule_id=rule_id,
        folder_prefix=folder_prefix,
        limit=limit,
        group_id=identity.group_id,
    )
    return {"contracts": [_jsonable(r) for r in rows]}


# --- helpers ------------------------------------------------------------------

def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce UUIDs, dates, decimals to JSON-friendly primitives."""
    from datetime import date, datetime
    from decimal import Decimal
    from uuid import UUID

    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        else:
            out[k] = v
    return out


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    settings = get_settings()
    log.info("query MCP server starting on %s:%s", settings.query_mcp_host, settings.query_mcp_port)
    mcp.run(
        transport="streamable-http",
        host=settings.query_mcp_host,
        port=settings.query_mcp_port,
    )


if __name__ == "__main__":
    main()
