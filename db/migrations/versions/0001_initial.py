"""Initial schema: documents, jobs, contracts, chunks.

Revision ID: 0001
Revises:
Create Date: 2026-04-26
"""
from __future__ import annotations

import os

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EMBEDDING_DIM = int(os.environ.get("VOYAGE_EMBEDDING_DIMENSIONS", "1024"))


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("content_hash", sa.Text, nullable=False, unique=True),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("mime_type", sa.Text, nullable=False, server_default="application/pdf"),
        sa.Column("byte_size", sa.BigInteger, nullable=False),
        sa.Column("rule_id", sa.Text, nullable=False),
        sa.Column("rule_version", sa.Text, nullable=False),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("group_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_documents_rule", "documents", ["rule_id", "rule_version"])
    op.create_index("ix_documents_group", "documents", ["group_id"])

    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=True),
        sa.Column("content_hash", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("rule_id", sa.Text, nullable=True),
        sa.Column("rule_version", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("group_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')",
            name="jobs_status_check",
        ),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index(
        "ix_jobs_pending_hash",
        "jobs",
        ["content_hash"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        "contracts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.Text, nullable=False),
        sa.Column("rule_version", sa.Text, nullable=False),
        # Promoted common scalar fields for fast filtering / cross-rule queries.
        sa.Column("parties", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("effective_date", sa.Date, nullable=True),
        sa.Column("expiry_date", sa.Date, nullable=True),
        sa.Column("currency", sa.Text, nullable=True),
        sa.Column("annual_value", sa.Numeric(18, 2), nullable=True),
        # Rule-specific structured payload.
        sa.Column("extracted", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        # Clause-presence flags + evidence. Shape: {flag_name: bool, flag_name_evidence: str?}.
        sa.Column("clauses", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        # Per-field source linkback. Shape: {field_name: {page, char_start, char_end, quote}}.
        sa.Column("source_links", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        # Raw model response for debugging and re-validation under future schemas.
        sa.Column("raw_response", postgresql.JSONB, nullable=True),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("group_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("document_id", "rule_id", "rule_version",
                            name="uq_contracts_doc_rule_version"),
    )
    op.create_index("ix_contracts_rule", "contracts", ["rule_id", "rule_version"])
    op.create_index("ix_contracts_expiry", "contracts", ["expiry_date"])
    op.create_index("ix_contracts_group", "contracts", ["group_id"])
    op.create_index("ix_contracts_clauses_gin", "contracts", ["clauses"],
                    postgresql_using="gin")
    op.create_index("ix_contracts_extracted_gin", "contracts", ["extracted"],
                    postgresql_using="gin")

    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("page_start", sa.Integer, nullable=True),
        sa.Column("page_end", sa.Integer, nullable=True),
        sa.Column("char_start", sa.Integer, nullable=True),
        sa.Column("char_end", sa.Integer, nullable=True),
        sa.Column("rule_id", sa.Text, nullable=False),
        sa.Column("rule_version", sa.Text, nullable=False),
        sa.Column("user_id", sa.Text, nullable=False),
        sa.Column("group_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunks_doc_idx"),
    )
    op.execute(
        f"ALTER TABLE chunks ADD COLUMN embedding vector({EMBEDDING_DIM})"
    )
    op.create_index("ix_chunks_group", "chunks", ["group_id"])
    # IVFFLAT index added after some data exists; HNSW would be the modern choice but
    # requires pgvector >= 0.5. Using HNSW with cosine distance.
    op.execute(
        "CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.drop_table("chunks")
    op.drop_table("contracts")
    op.drop_table("jobs")
    op.drop_table("documents")
