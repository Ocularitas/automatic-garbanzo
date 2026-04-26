"""Query MCP server. Streamable-HTTP transport, FastMCP."""
from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from sqlalchemy import text

from ingestion.embedder import embed_query
from mcp_servers.query import store
from rules.registry import all_rules, get_rule
from shared.config import get_settings
from shared.db import session_scope
from shared.identity import current_identity

log = logging.getLogger(__name__)

mcp = FastMCP("contract-intelligence-query")


# --- Schema introspection ---------------------------------------------------

def _type_str(annotation: Any) -> str:
    """Render a Pydantic field annotation as a short, LLM-readable type string."""
    s = str(annotation)
    for prefix in ("typing.", "datetime.", "decimal."):
        s = s.replace(prefix, "")
    s = s.replace("NoneType", "null").replace(" | None", " | null")
    return s


def _build_schema_payload(include_corpus: bool = True) -> dict[str, Any]:
    """Single source of truth for schema introspection — used by both the
    `describe_schema` tool and the MCP resources."""
    rules_out: list[dict[str, Any]] = []
    for rule_id, rule in all_rules().items():
        fields: list[dict[str, Any]] = []
        for name, info in rule.fields_model.model_fields.items():
            fields.append({
                "name": name,
                "type": _type_str(info.annotation),
                "description": info.description or "",
                "required": info.is_required(),
            })
        clause_flags: list[dict[str, Any]] = []
        for name, info in rule.clauses_model.model_fields.items():
            if name.endswith("_evidence"):
                # Evidence fields are paired with flags; documenting one is enough.
                continue
            clause_flags.append({
                "name": name,
                "description": info.description or "",
                "evidence_field": f"{name}_evidence",
            })
        rules_out.append({
            "rule_id": rule_id,
            "version": rule.version,
            "description": rule.description,
            "fields": fields,
            "clause_flags": clause_flags,
            "promoted_scalar_columns": list(rule.promoted_fields),
        })

    payload: dict[str, Any] = {
        "rules": rules_out,
        "query_filter_operators": _query_filter_operators(),
        "record_envelope": _record_envelope(),
    }

    if include_corpus:
        identity = current_identity()
        with session_scope() as s:
            total = s.execute(
                text("SELECT COUNT(*) FROM contracts WHERE group_id = :g"),
                {"g": identity.group_id},
            ).scalar() or 0
            by_rule_rows = s.execute(
                text("""
                    SELECT rule_id, rule_version, COUNT(*) AS n
                      FROM contracts
                     WHERE group_id = :g
                     GROUP BY rule_id, rule_version
                """),
                {"g": identity.group_id},
            ).mappings().all()
        by_rule: dict[str, dict[str, int]] = {}
        for row in by_rule_rows:
            by_rule.setdefault(row["rule_id"], {})[row["rule_version"]] = row["n"]
        payload["corpus"] = {
            "total_contracts": int(total),
            "by_rule_version": by_rule,
        }
    return payload


def _query_filter_operators() -> list[dict[str, str]]:
    """The full operator vocabulary `query_contracts_structured` accepts.

    Sourced once here so tool descriptions don't have to repeat it. Adding an
    operator means adding a row here and a branch in store.query_contracts_structured.
    """
    return [
        {"op": "eq",      "description": "Equality. Shorthand: bare value (no operator wrapper) is treated as eq."},
        {"op": "ne",      "description": "Not equal."},
        {"op": "lt",      "description": "Less than. For dates and numbers."},
        {"op": "lte",     "description": "Less than or equal."},
        {"op": "gt",      "description": "Greater than."},
        {"op": "gte",     "description": "Greater than or equal."},
        {"op": "in",      "description": 'Value is in a non-empty list, e.g. {"in": ["GBP", "USD"]}.'},
        {"op": "like",    "description": "SQL ILIKE pattern match (case-insensitive). Use % wildcards."},
        {"op": "is_null", "description": 'Pass true to require null, false to require not-null.'},
    ]


def _record_envelope() -> dict[str, Any]:
    """How a persisted contract record is shaped on read.

    Useful for callers of `get_contract` and `query_contracts_structured` that
    otherwise have to learn the shape by inspecting an example record.
    """
    return {
        "summary": (
            "A contract record is split across one set of typed scalar columns "
            "and four JSONB blobs. Read from the column most appropriate to "
            "the question."
        ),
        "promoted_columns": {
            "description": (
                "Indexed scalar columns lifted out of the extraction. Use these "
                "for filtering, sorting, and any typed numeric/date read. They "
                "are also the only columns `query_contracts_structured` accepts "
                "as filter targets (alongside `clauses.<flag>`)."
            ),
            "names": ["parties", "effective_date", "expiry_date", "currency", "annual_value"],
        },
        "extracted": {
            "description": (
                "Full Pydantic dump of the rule's Fields model. Includes every "
                "field declared by the active rule version. Same field names "
                "as the rule's `fields[*].name`."
            ),
            "decimal_serialization": (
                "Decimal values are serialized as JSON strings to preserve "
                'precision (e.g. "145000" rather than 145000.0). For typed '
                "numeric reads, prefer the promoted column. If reading from "
                "`extracted`, coerce explicitly."
            ),
        },
        "clauses": {
            "description": (
                "Clause-checklist booleans and their evidence strings. For each "
                "clause flag named in `rules[*].clause_flags`, this object holds "
                "two keys: `<flag_name>` (bool) and `<flag_name>_evidence` "
                "(string or null). The evidence string is the verbatim quote "
                "from the document."
            ),
        },
        "source_links": {
            "description": (
                "Map of field name to {page, char_start, char_end, quote}. "
                "Populated for any clause flag set true (the same verbatim quote "
                "appears here and in `clauses.<flag>_evidence`) and for any "
                "Field-model field the model chose to cite."
            ),
        },
        "raw_response": {
            "description": (
                "The complete Anthropic API response from the most recent "
                "extraction. Useful for debugging extraction quality; not "
                "intended as a query target."
            ),
        },
    }


@mcp.tool()
def describe_schema() -> dict[str, Any]:
    """Describe the active rules, their fields, clause flags, the filter
    operator vocabulary, the persisted record envelope, and the corpus shape.

    Call this once at the start of a session to learn what's queryable before
    composing other tools. Returns:
      - `rules`: every active rule with `rule_id`, `version`, `description`,
        plus the full `fields` list (name + type + description) and the
        `clause_flags` list (name + description + paired evidence field name).
      - `query_filter_operators`: the operator vocabulary
        `query_contracts_structured` accepts (eq, ne, lt, lte, gt, gte, in,
        like, is_null), each with a one-line description.
      - `record_envelope`: how a persisted contract record is structured on
        read — promoted columns vs `extracted` / `clauses` / `source_links` /
        `raw_response` JSONB blobs, including the Decimal-serialization
        convention (string in `extracted`, float in promoted columns).
      - `corpus`: total contract count and a breakdown by rule_id/rule_version.

    The schema reflects the live deployment, so if a rule has been bumped or
    new fields added, calling this is the cheapest way to find out — no need
    to fish through example records.
    """
    return _build_schema_payload(include_corpus=True)


# --- Resources (mirror of the same data for resource-aware clients) ---------

@mcp.resource("schema://rules")
def resource_rules() -> dict[str, Any]:
    """All active rules, summarised. No corpus stats."""
    return _build_schema_payload(include_corpus=False)


@mcp.resource("schema://rules/{rule_id}")
def resource_rule(rule_id: str) -> dict[str, Any]:
    """Full schema for one rule by rule_id."""
    rule = get_rule(rule_id)
    payload = _build_schema_payload(include_corpus=False)
    matched = next((r for r in payload["rules"] if r["rule_id"] == rule_id), None)
    if matched is None:
        raise KeyError(rule_id)
    return matched


@mcp.resource("schema://corpus")
def resource_corpus() -> dict[str, Any]:
    """Just the corpus shape — total + per-rule_version counts."""
    return _build_schema_payload(include_corpus=True)["corpus"]


# --- Query tools ------------------------------------------------------------

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

    If you need to know which `rule_id` values are valid, call
    `describe_schema` first — it returns the live list.

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
      - Equality (shorthand): {"rule_id": "saas_contract"}
      - Operator wrapper:    {"expiry_date": {"lte": "2026-06-30"}}
      - List membership:     {"currency": {"in": ["GBP", "USD"]}}
      - Clause flags:        {"clauses.has_dr_clause": false}

    Allowed filter targets: rule_id, rule_version, effective_date, expiry_date,
    currency, annual_value, file_path, plus clauses.<flag_name>.
    Allowed operators: see `describe_schema().query_filter_operators` for the
    full vocabulary with descriptions.

    Select shape (the `select` arg projects the result rows):
      - Top-level field:  "expiry_date"        — returns the column.
      - Dotted JSONB path:
          "extracted.data_breach_notification_window_hours"
          "clauses.has_dr_clause"
          "source_links.has_dr_clause"
        Returns the leaf value, keyed by the dotted name in the response.
      - Bare leaf name:   "data_breach_notification_window_hours"
        Resolved against `extracted` then `clauses`.
      - Unknown selectors raise an error rather than being silently dropped,
        so a null in the response means "no value", not "you typed it wrong."
      - `contract_id`, `document_id`, `file_path` are always included.

    Call `describe_schema` first if you don't know which clause flags exist,
    which rule versions are in the corpus, or how a persisted record is
    structured — the live answers are all there.

    Args:
        filters: Filter dict matching the shapes above.
        select: Optional list of fields/paths to project; otherwise the full
            row (including the JSONB blobs) is returned.
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
def get_clause_evidence(
    clause_flag: str,
    rule_id: str | None = None,
    folder_prefix: str | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Find every contract where a named clause is *present*, with the evidence.

    Inverse of `find_clause_gaps`. Use this for positive-evidence questions:
    "show me what every SaaS contract says about indemnity caps" or
    "which leases have a break clause and what's the exact wording?".

    Returns one row per contract with parties, expiry, the verbatim clause
    quote, and the page number — in one call. Prefer this over running
    `query_contracts_structured` then `get_contract` per row when all you
    need is the clause language.

    `clause_flag` must match a flag in the active rule's checklist — call
    `describe_schema` to see the list (`rules[*].clause_flags[*].name`).

    Args:
        clause_flag: The clause flag to look for, e.g. "has_indemnity_cap".
        rule_id: Optional restriction to one rule.
        folder_prefix: Optional substring filter on file path.
        limit: Max results, default 200.
    """
    limit = max(1, min(limit, 500))
    identity = current_identity()
    rows = store.get_clause_evidence(
        clause_flag=clause_flag,
        rule_id=rule_id,
        folder_prefix=folder_prefix,
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

    Call `describe_schema` first if you need the list of valid clause flags
    for the active rules.

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
