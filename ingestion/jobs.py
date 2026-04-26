"""Job-queue operations.

Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED` to allow safe
parallel workers without bespoke queue infrastructure. The job table is the
source of truth for processing state.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def enqueue_job(
    session: Session,
    *,
    content_hash: str,
    file_path: str,
    user_id: str,
    group_id: str,
) -> UUID | None:
    """Insert a pending job for this content hash, unless one already exists
    in pending/running state. Returns the new job id, or None if dedup'd."""
    row = session.execute(
        text(
            """
            INSERT INTO jobs (content_hash, file_path, status, user_id, group_id)
            VALUES (:content_hash, :file_path, 'pending', :user_id, :group_id)
            ON CONFLICT (content_hash) WHERE status IN ('pending', 'running')
            DO NOTHING
            RETURNING id
            """
        ),
        {
            "content_hash": content_hash,
            "file_path": file_path,
            "user_id": user_id,
            "group_id": group_id,
        },
    ).first()
    return row[0] if row else None


def claim_next_job(session: Session) -> dict[str, Any] | None:
    """Atomically claim the oldest pending job. Returns its row as a dict."""
    row = session.execute(
        text(
            """
            WITH claimed AS (
                SELECT id FROM jobs
                WHERE status = 'pending'
                ORDER BY created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE jobs
            SET status = 'running',
                started_at = now(),
                attempt_count = attempt_count + 1
            WHERE id = (SELECT id FROM claimed)
            RETURNING id, content_hash, file_path, user_id, group_id, attempt_count
            """
        )
    ).mappings().first()
    return dict(row) if row else None


def mark_done(session: Session, job_id: UUID, document_id: UUID, rule_id: str, rule_version: str) -> None:
    session.execute(
        text(
            """
            UPDATE jobs
            SET status = 'done',
                completed_at = now(),
                document_id = :document_id,
                rule_id = :rule_id,
                rule_version = :rule_version,
                error_message = NULL
            WHERE id = :id
            """
        ),
        {
            "id": job_id,
            "document_id": document_id,
            "rule_id": rule_id,
            "rule_version": rule_version,
        },
    )


def mark_failed(session: Session, job_id: UUID, error_message: str) -> None:
    session.execute(
        text(
            """
            UPDATE jobs
            SET status = 'failed',
                completed_at = now(),
                error_message = :error_message
            WHERE id = :id
            """
        ),
        {"id": job_id, "error_message": error_message[:4000]},
    )


def requeue_failed(session: Session, job_id: UUID | None = None) -> int:
    """Move failed jobs back to pending. If job_id is None, requeue all."""
    if job_id is None:
        result = session.execute(
            text("UPDATE jobs SET status = 'pending', error_message = NULL "
                 "WHERE status = 'failed'")
        )
    else:
        result = session.execute(
            text("UPDATE jobs SET status = 'pending', error_message = NULL "
                 "WHERE status = 'failed' AND id = :id"),
            {"id": job_id},
        )
    return result.rowcount or 0
