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
               c.annual_value, c.clauses, c.extracted, d.file_path
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
        allowed_keys = set(select_fields) | {"contract_id", "document_id", "file_path"}
        rows = [{k: v for k, v in r.items() if k in allowed_keys} for r in rows]

    return rows, next_cursor


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
               c.clauses ->> '{evidence_key}'         AS evidence,
               c.source_links -> '{clause_flag}' ->> 'page'  AS page,
               c.source_links -> '{clause_flag}' ->> 'quote' AS source_quote,
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
