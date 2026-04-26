"""End-to-end ingestion for one document."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from ingestion import jobs, writer
from ingestion.chunker import chunk_text
from ingestion.embedder import embed_documents
from ingestion.extractor import extract_contract
from ingestion.hashing import hash_file
from ingestion.parser import parse_pdf
from rules.registry import resolve_rule_for_path
from shared.db import session_scope
from shared.identity import current_identity

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    document_id: UUID
    contract_id: UUID
    rule_id: str
    rule_version: str
    num_chunks: int


def process_file(path: Path) -> PipelineResult:
    """Process a single PDF end-to-end. Idempotent on content_hash."""
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() != ".pdf":
        raise ValueError(
            f"Only PDFs supported in POC; got {path.suffix} for {path.name}"
        )

    identity = current_identity()
    rule = resolve_rule_for_path(path)
    content_hash = hash_file(path)
    log.info("processing %s rule=%s/%s hash=%s",
             path.name, rule.rule_id, rule.version, content_hash[:12])

    parsed = parse_pdf(path)
    extraction = extract_contract(rule, path)
    chunks = chunk_text(parsed)
    embeddings = embed_documents([c.text for c in chunks])

    with session_scope() as session:
        document_id = writer.upsert_document(
            session,
            content_hash=content_hash,
            file_path=path,
            byte_size=path.stat().st_size,
            rule=rule,
            user_id=identity.user_id,
            group_id=identity.group_id,
        )
        contract_id = writer.write_contract(
            session,
            document_id=document_id,
            rule=rule,
            result=extraction,
            user_id=identity.user_id,
            group_id=identity.group_id,
        )
        n_chunks = writer.replace_chunks(
            session,
            document_id=document_id,
            rule=rule,
            chunks=chunks,
            embeddings=embeddings,
            user_id=identity.user_id,
            group_id=identity.group_id,
        )

    return PipelineResult(
        document_id=document_id,
        contract_id=contract_id,
        rule_id=rule.rule_id,
        rule_version=rule.version,
        num_chunks=n_chunks,
    )


def process_job(job: dict) -> PipelineResult:
    """Run the pipeline for a claimed job, updating job state on success/failure."""
    job_id = job["id"]
    file_path = Path(job["file_path"])
    try:
        result = process_file(file_path)
    except Exception as e:
        log.exception("job %s failed", job_id)
        with session_scope() as session:
            jobs.mark_failed(session, job_id, f"{type(e).__name__}: {e}")
        raise

    with session_scope() as session:
        jobs.mark_done(
            session,
            job_id=job_id,
            document_id=result.document_id,
            rule_id=result.rule_id,
            rule_version=result.rule_version,
        )
    return result
