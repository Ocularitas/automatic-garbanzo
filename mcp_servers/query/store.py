"""Read-side queries against Postgres.

Kept separate from the MCP-tool layer so the SQL is testable in isolation
and so an alternative front end (HTTP, gRPC, CLI) could reuse it later.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from shared.db import session_scope
from shared.urls import build_document_url


# --- Vector search ------------------------------------------------------------

@dataclass
class ChunkHit:
    document_id: UUID
    chunk_id: UUID
    chunk_index: int
    text: str
    page_start: int | None
    page_end: int | None
    score: float
    rule_id: str
    file_path: str


def vector_search(
    *,
    query_embedding: list[float],
    top_k: int = 8,
    folder_prefix: str | None = None,
    rule_id: str | None = None,
    group_id: str,
) -> list[ChunkHit]:
    vec_literal = "[" + ",".join(f"{x:.7f}" for x in query_embedding) + "]"
    sql = """
        SELECT c.id AS chunk_id, c.document_id, c.chunk_index, c.text,
               c.page_start, c.page_end, c.rule_id,
               d.file_path,
               1 - (c.embedding <=> CAST(:vec AS vector)) AS score
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.group_id = :group_id
        {folder_clause}
        {rule_clause}
        ORDER BY c.embedding <=> CAST(:vec AS vector)
        LIMIT :top_k
    """
    folder_clause = "AND d.file_path LIKE :folder_pat" if folder_prefix else ""
    rule_clause = "AND c.rule_id = :rule_id" if rule_id else ""
    sql = sql.format(folder_clause=folder_clause, rule_clause=rule_clause)

    params: dict[str, Any] = {"vec": vec_literal, "top_k": top_k, "group_id": group_id}
    if folder_prefix:
        params["folder_pat"] = f"%{folder_prefix}%"
    if rule_id:
        params["rule_id"] = rule_id

    with session_scope() as session:
        rows = session.execute(text(sql), params).mappings().all()
    return [ChunkHit(**dict(r)) for r in rows]


# --- Structured query ---------------------------------------------------------

# Whitelist of fields the structured query accepts. Anything else is rejected.
SCALAR_FIELDS: dict[str, str] = {
    "rule_id":         "c.rule_id",
    "rule_version":    "c.rule_version",
    "effective_date":  "c.effective_date",
    "expiry_date":     "c.expiry_date",
    "currency":        "c.currency",
    "annual_value":    "c.annual_value",
    "file_path":       "d.file_path",
}
DATE_FIELDS = {"effective_date", "expiry_date"}
NUMERIC_FIELDS = {"annual_value"}
ALLOWED_OPS = {"eq", "ne", "lt", "lte", "gt", "gte", "in", "like", "is_null"}


def query_contracts_structured(
    *,
    filters: dict[str, Any],
    select_fields: list[str] | None,
    limit: int,
    cursor: str | None,
    group_id: str,
) -> tuple[list[dict[str, Any]], str | None]:
    """Filter and project the contracts table.

    `filters` shape: {field: value} for equality, or {field: {op: value}} for
    operators. `clauses.<flag>` filters the JSONB column.
    """
    where_parts: list[str] = ["c.group_id = :group_id"]
    params: dict[str, Any] = {"group_id": group_id, "limit": limit + 1}

    cursor_clause = ""
    if cursor:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
            cursor_clause = "AND c.id > :cursor_id"
            params["cursor_id"] = decoded["id"]
        except Exception as e:
            raise ValueError(f"Invalid cursor: {e}") from e

    for i, (field, raw) in enumerate(filters.items()):
        if field.startswith("clauses."):
            flag = field.split(".", 1)[1]
            if not _safe_ident(flag):
                raise ValueError(f"Invalid clause name: {flag}")
            key = f"clause_{i}"
            where_parts.append(f"(c.clauses ->> '{flag}')::bool = :{key}")
            params[key] = bool(_unwrap_eq(raw))
            continue

        if field not in SCALAR_FIELDS:
            raise ValueError(
                f"Unknown field: {field}. Allowed: {sorted(SCALAR_FIELDS)} "
                f"or clauses.<flag>"
            )
        col = SCALAR_FIELDS[field]
        op, value = _parse_op(raw)
        if op not in ALLOWED_OPS:
            raise ValueError(f"Unsupported operator {op!r}; allowed: {sorted(ALLOWED_OPS)}")
        key = f"p_{i}"
        if op == "is_null":
            where_parts.append(f"{col} IS NULL" if bool(value) else f"{col} IS NOT NULL")
        elif op == "in":
            if not isinstance(value, list) or not value:
                raise ValueError(f"`in` requires a non-empty list for {field}")
            where_parts.append(f"{col} = ANY(:{key})")
            params[key] = [_coerce(field, v) for v in value]
        elif op == "like":
            where_parts.append(f"{col} ILIKE :{key}")
            params[key] = str(value)
        else:
            sql_op = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=",
                      "gt": ">", "gte": ">="}[op]
            where_parts.append(f"{col} {sql_op} :{key}")
            params[key] = _coerce(field, value)

    where_sql = " AND ".join(where_parts)
    if cursor_clause:
        where_sql += " " + cursor_clause

    sql = f"""
        SELECT c.id AS contract_id, c.document_id, c.rule_id, c.rule_version,
               c.parties, c.effective_date, c.expiry_date, c.currency,
               c.annual_value, c.clauses, c.extracted, c.source_links, d.file_path
        FROM contracts c
        JOIN documents d ON d.id = c.document_id
        WHERE {where_sql}
        ORDER BY c.id
        LIMIT :limit
    """

    with session_scope() as session:
        rows = session.execute(text(sql), params).mappings().all()

    rows = [dict(r) for r in rows]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        rows = rows[:limit]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps({"id": str(last["contract_id"])}).encode()
        ).decode()

    if select_fields:
        rows = _project_select(rows, select_fields)

    return rows, next_cursor


# Top-level keys returned by query_contracts_structured's SQL.
TOP_LEVEL_KEYS = frozenset({
    "contract_id", "document_id", "file_path",
    "rule_id", "rule_version", "parties",
    "effective_date", "expiry_date", "currency", "annual_value",
    "clauses", "extracted", "source_links",
})

# JSONB containers a dotted-path can index into.
JSONB_CONTAINERS = frozenset({"extracted", "clauses", "source_links"})

# Always included in projected rows so callers can re-identify hits.
ALWAYS_KEEP = ("contract_id", "document_id", "file_path")


def _project_select(rows: list[dict[str, Any]],
                    select_fields: list[str]) -> list[dict[str, Any]]:
    """Project rows to the requested fields. Three forms accepted:

      * Top-level field name, e.g. "expiry_date" — returns the column.
      * Dotted path into a JSONB container, e.g. "extracted.data_breach_notification_window_hours"
        or "clauses.has_dr_clause" — returns the leaf, keyed by the dotted name.
      * Bare leaf name resolved against `extracted`, then `clauses`. Useful for
        callers who don't want to know which container a field lives in.

    Unknown selectors raise ValueError. The earlier silent-drop behaviour
    made it impossible to tell "field is null" from "select was malformed".

    Always emits `contract_id`, `document_id`, `file_path` so callers can
    re-identify rows.

    For any selector that resolves to a clause flag (bare `has_*`, dotted
    `clauses.has_*`, or the matching `_evidence` field), this also injects
    `<flag>_source_url` — a page-anchored URL constructed from the entry in
    `source_links.<flag>` so a chat client can cite directly without a
    follow-up round trip.
    """
    out_rows: list[dict[str, Any]] = []
    for row in rows:
        out: dict[str, Any] = {k: row.get(k) for k in ALWAYS_KEEP}
        flag_urls_to_add: set[str] = set()

        for sel in select_fields:
            value, output_key = _resolve_select_target(sel, row)
            out[output_key] = value

            flag = _clause_flag_from_selector(sel, row)
            if flag:
                flag_urls_to_add.add(flag)

        for flag in flag_urls_to_add:
            url = _flag_source_url(row, flag)
            if url:
                out[f"{flag}_source_url"] = url

        out_rows.append(out)
    return out_rows


def _clause_flag_from_selector(sel: str, row: dict[str, Any]) -> str | None:
    """If a selector resolves to a clause-checklist field, return the flag name
    (with any `_evidence` suffix stripped). Otherwise return None."""
    if "." in sel:
        container, _, leaf = sel.partition(".")
        if container != "clauses":
            return None
        flag = leaf
    else:
        clauses = row.get("clauses") or {}
        if sel not in clauses:
            return None
        flag = sel

    if flag.endswith("_evidence"):
        flag = flag[: -len("_evidence")]
    return flag


def _flag_source_url(row: dict[str, Any], flag: str) -> str | None:
    """Construct a page-anchored URL for a clause flag, using the row's
    `source_links.<flag>.page` and `file_path`.

    Returns None if `source_links` has no entry for the flag — the row-level
    `document_url` is still available as a fallback document-level citation."""
    source_links = row.get("source_links") or {}
    link = source_links.get(flag)
    if not isinstance(link, dict):
        return None
    raw_page = link.get("page")
    try:
        page = int(raw_page) if raw_page is not None else None
    except (TypeError, ValueError):
        page = None
    return build_document_url(row.get("file_path"), page=page)


def _resolve_select_target(sel: str, row: dict[str, Any]) -> tuple[Any, str]:
    """Resolve a single select expression against one row.

    Returns (value, output_key). Raises ValueError on unknown."""
    if "." in sel:
        container, _, leaf = sel.partition(".")
        if container not in JSONB_CONTAINERS:
            raise ValueError(
                f"Unknown select target {sel!r}. Dotted paths must start with one of "
                f"{sorted(JSONB_CONTAINERS)}, got {container!r}."
            )
        if not _safe_ident(leaf):
            raise ValueError(f"Invalid leaf name in select: {leaf!r}")
        blob = row.get(container) or {}
        return blob.get(leaf), sel  # output keyed by the dotted name

    # Bare name — resolve against top-level, then extracted, then clauses.
    if sel in TOP_LEVEL_KEYS:
        return row.get(sel), sel
    extracted = row.get("extracted") or {}
    if sel in extracted:
        return extracted.get(sel), sel
    clauses = row.get("clauses") or {}
    if sel in clauses:
        return clauses.get(sel), sel

    raise ValueError(
        f"Unknown select target {sel!r}. Accepted forms: "
        f"a top-level field ({', '.join(sorted(TOP_LEVEL_KEYS))}), "
        f"a dotted path 'extracted.<name>' / 'clauses.<name>' / "
        f"'source_links.<name>', or a bare field name that exists in "
        f"`extracted` or `clauses` for the active rule (see describe_schema)."
    )


def get_contract(contract_id: UUID, group_id: str) -> dict[str, Any] | None:
    with session_scope() as session:
        row = session.execute(
            text(
                """
                SELECT c.*, d.file_path, d.content_hash
                FROM contracts c
                JOIN documents d ON d.id = c.document_id
                WHERE c.id = :id AND c.group_id = :group_id
                """
            ),
            {"id": contract_id, "group_id": group_id},
        ).mappings().first()
    return dict(row) if row else None


def list_contracts(
    *, folder_prefix: str | None, rule_id: str | None, limit: int, group_id: str
) -> list[dict[str, Any]]:
    sql = """
        SELECT c.id AS contract_id, c.document_id, c.rule_id, c.rule_version,
               c.parties, c.effective_date, c.expiry_date,
               c.currency, c.annual_value, d.file_path
        FROM contracts c
        JOIN documents d ON d.id = c.document_id
        WHERE c.group_id = :group_id
        {folder_clause}
        {rule_clause}
        ORDER BY c.created_at DESC
        LIMIT :limit
    """
    folder_clause = "AND d.file_path LIKE :folder_pat" if folder_prefix else ""
    rule_clause = "AND c.rule_id = :rule_id" if rule_id else ""
    sql = sql.format(folder_clause=folder_clause, rule_clause=rule_clause)

    params: dict[str, Any] = {"group_id": group_id, "limit": limit}
    if folder_prefix:
        params["folder_pat"] = f"%{folder_prefix}%"
    if rule_id:
        params["rule_id"] = rule_id

    with session_scope() as session:
        rows = session.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def get_clause_evidence(
    *,
    clause_flag: str,
    rule_id: str | None,
    folder_prefix: str | None,
    limit: int,
    group_id: str,
) -> list[dict[str, Any]]:
    """Every contract where the named clause flag is true, with its evidence.

    Returns one row per contract: parties, expiry, the verbatim quote, and the
    page where it was found. Combines `clauses.<flag>=true`, the matching
    `<flag>_evidence` text in the clauses JSONB, and the per-flag entry in
    `source_links`. Single SQL query — no N+1 round trip required.
    """
    if not _safe_ident(clause_flag):
        raise ValueError(f"Invalid clause name: {clause_flag}")
    evidence_key = f"{clause_flag}_evidence"
    sql = f"""
        SELECT c.id AS contract_id, c.document_id,
               c.rule_id, c.rule_version, c.parties, c.expiry_date,
               -- Single canonical evidence string. The rule prompt asks the
               -- model to populate both clauses.<flag>_evidence and
               -- source_links.<flag>.quote with the same verbatim quote;
               -- COALESCE so we still surface a value if one side is missing.
               COALESCE(
                   c.clauses ->> '{evidence_key}',
                   c.source_links -> '{clause_flag}' ->> 'quote'
               ) AS evidence,
               c.source_links -> '{clause_flag}' ->> 'page'  AS page,
               d.file_path
          FROM contracts c
          JOIN documents d ON d.id = c.document_id
         WHERE c.group_id = :group_id
           AND COALESCE((c.clauses ->> '{clause_flag}')::bool, false) = true
        {("AND c.rule_id = :rule_id" if rule_id else "")}
        {("AND d.file_path LIKE :folder_pat" if folder_prefix else "")}
         ORDER BY c.created_at DESC
         LIMIT :limit
    """
    params: dict[str, Any] = {"group_id": group_id, "limit": limit}
    if rule_id:
        params["rule_id"] = rule_id
    if folder_prefix:
        params["folder_pat"] = f"%{folder_prefix}%"
    with session_scope() as session:
        rows = session.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def find_clause_gaps(
    *,
    clause_flag: str,
    rule_id: str | None,
    folder_prefix: str | None,
    limit: int,
    group_id: str,
) -> list[dict[str, Any]]:
    if not _safe_ident(clause_flag):
        raise ValueError(f"Invalid clause name: {clause_flag}")
    sql = f"""
        SELECT c.id AS contract_id, c.document_id, c.rule_id, c.rule_version,
               c.parties, c.expiry_date, d.file_path
        FROM contracts c
        JOIN documents d ON d.id = c.document_id
        WHERE c.group_id = :group_id
          AND COALESCE((c.clauses ->> '{clause_flag}')::bool, false) = false
        {("AND c.rule_id = :rule_id" if rule_id else "")}
        {("AND d.file_path LIKE :folder_pat" if folder_prefix else "")}
        ORDER BY c.created_at DESC
        LIMIT :limit
    """
    params: dict[str, Any] = {"group_id": group_id, "limit": limit}
    if rule_id:
        params["rule_id"] = rule_id
    if folder_prefix:
        params["folder_pat"] = f"%{folder_prefix}%"
    with session_scope() as session:
        rows = session.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


# --- helpers ------------------------------------------------------------------

def _parse_op(raw: Any) -> tuple[str, Any]:
    if isinstance(raw, dict) and len(raw) == 1:
        op, value = next(iter(raw.items()))
        return op, value
    return "eq", raw


def _unwrap_eq(raw: Any) -> Any:
    if isinstance(raw, dict) and len(raw) == 1:
        op, value = next(iter(raw.items()))
        if op != "eq":
            raise ValueError("clause filters only support equality")
        return value
    return raw


def _coerce(field: str, value: Any) -> Any:
    if field in DATE_FIELDS and isinstance(value, str):
        return date.fromisoformat(value)
    if field in NUMERIC_FIELDS and isinstance(value, (int, float, str)):
        return Decimal(str(value))
    return value


def _safe_ident(name: str) -> bool:
    return name.replace("_", "").isalnum() and not name.startswith("_")
