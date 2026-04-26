"""DB writes for ingestion results."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from ingestion.chunker import Chunk
from ingestion.extractor import ExtractionResult
from shared.models import Rule


def upsert_document(
    session: Session,
    *,
    content_hash: str,
    file_path: Path,
    byte_size: int,
    rule: Rule,
    user_id: str,
    group_id: str,
) -> UUID:
    row = session.execute(
        text(
            """
            INSERT INTO documents (
                content_hash, file_path, mime_type, byte_size,
                rule_id, rule_version, user_id, group_id
            )
            VALUES (
                :content_hash, :file_path, 'application/pdf', :byte_size,
                :rule_id, :rule_version, :user_id, :group_id
            )
            ON CONFLICT (content_hash) DO UPDATE
            SET file_path = EXCLUDED.file_path,
                rule_id = EXCLUDED.rule_id,
                rule_version = EXCLUDED.rule_version,
                updated_at = now()
            RETURNING id
            """
        ),
        {
            "content_hash": content_hash,
            "file_path": str(file_path),
            "byte_size": byte_size,
            "rule_id": rule.rule_id,
            "rule_version": rule.version,
            "user_id": user_id,
            "group_id": group_id,
        },
    ).first()
    assert row is not None
    return row[0]


def write_contract(
    session: Session,
    *,
    document_id: UUID,
    rule: Rule,
    result: ExtractionResult,
    user_id: str,
    group_id: str,
) -> UUID:
    fields_dump = result.fields.model_dump(mode="json")
    promoted = {name: fields_dump.get(name) for name in rule.promoted_fields}
    clauses_dump = result.clauses.model_dump(mode="json")

    row = session.execute(
        text(
            """
            INSERT INTO contracts (
                document_id, rule_id, rule_version,
                parties, effective_date, expiry_date, currency, annual_value,
                extracted, clauses, source_links, raw_response,
                user_id, group_id
            )
            VALUES (
                :document_id, :rule_id, :rule_version,
                :parties, :effective_date, :expiry_date, :currency, :annual_value,
                CAST(:extracted AS jsonb), CAST(:clauses AS jsonb),
                CAST(:source_links AS jsonb), CAST(:raw_response AS jsonb),
                :user_id, :group_id
            )
            ON CONFLICT (document_id, rule_id, rule_version) DO UPDATE
            SET parties = EXCLUDED.parties,
                effective_date = EXCLUDED.effective_date,
                expiry_date = EXCLUDED.expiry_date,
                currency = EXCLUDED.currency,
                annual_value = EXCLUDED.annual_value,
                extracted = EXCLUDED.extracted,
                clauses = EXCLUDED.clauses,
                source_links = EXCLUDED.source_links,
                raw_response = EXCLUDED.raw_response
            RETURNING id
            """
        ),
        {
            "document_id": document_id,
            "rule_id": rule.rule_id,
            "rule_version": rule.version,
            "parties": promoted.get("parties"),
            "effective_date": promoted.get("effective_date"),
            "expiry_date": promoted.get("expiry_date"),
            "currency": promoted.get("currency"),
            "annual_value": promoted.get("annual_value"),
            "extracted": json.dumps(fields_dump),
            "clauses": json.dumps(clauses_dump),
            "source_links": json.dumps(result.source_links),
            "raw_response": json.dumps(result.raw_response),
            "user_id": user_id,
            "group_id": group_id,
        },
    ).first()
    assert row is not None
    return row[0]


def replace_chunks(
    session: Session,
    *,
    document_id: UUID,
    rule: Rule,
    chunks: list[Chunk],
    embeddings: list[list[float]],
    user_id: str,
    group_id: str,
) -> int:
    session.execute(
        text("DELETE FROM chunks WHERE document_id = :document_id"),
        {"document_id": document_id},
    )
    if not chunks:
        return 0
    assert len(chunks) == len(embeddings), "chunks/embeddings length mismatch"
    rows: list[dict[str, Any]] = []
    for chunk, vec in zip(chunks, embeddings):
        rows.append({
            "document_id": document_id,
            "chunk_index": chunk.index,
            "text": chunk.text,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "char_start": chunk.char_start,
            "char_end": chunk.char_end,
            "embedding": _vec_literal(vec),
            "rule_id": rule.rule_id,
            "rule_version": rule.version,
            "user_id": user_id,
            "group_id": group_id,
        })
    session.execute(
        text(
            """
            INSERT INTO chunks (
                document_id, chunk_index, text,
                page_start, page_end, char_start, char_end,
                embedding, rule_id, rule_version, user_id, group_id
            )
            VALUES (
                :document_id, :chunk_index, :text,
                :page_start, :page_end, :char_start, :char_end,
                CAST(:embedding AS vector), :rule_id, :rule_version, :user_id, :group_id
            )
            """
        ),
        rows,
    )
    return len(rows)


def _vec_literal(vec: list[float]) -> str:
    """pgvector text input format: '[1.0,2.0,...]'."""
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
