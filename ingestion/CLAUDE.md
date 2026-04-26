# Ingestion service

## Job

Watch a folder, detect new and changed files, run them through an extraction pipeline that applies the relevant rule set, and write structured records and document chunks to Postgres.

## Pipeline

```
File event ──► Hash check ──► Job created (status=pending)
                                       │
                                       ▼
                              Worker picks up job
                                       │
                                       ▼
                       Determine rule set (folder-based)
                                       │
                                       ▼
                            Parse PDF (Claude native)
                                       │
                            ┌──────────┴──────────┐
                            ▼                     ▼
                  Extract structured       Chunk and embed
                  (Claude tool use,        (with source
                   Pydantic schema)         linkback)
                            │                     │
                            ▼                     ▼
                     structured tables       chunks table
                            │
                            ▼
                      Job status = done
```

Each stage writes to the database and updates the job record. Failure at any stage marks the job failed with an error message; it stays failed until manually re-queued.

## Rule applicability

Folder-based, configured per top-level folder via `rules/folder_map.yaml`:

```yaml
folders:
  contracts/saas: saas_contract
  contracts/services: services_contract
  contracts/leases: lease
default: generic_contract
```

The map points to a `rule_id`. The current pinned major version for that `rule_id` is read from `rules/<rule_id>/current.yaml`. The ingestion service reads the map and the pinned version at startup and stamps every extraction record with the resolved `(rule_id, rule_version)`.

## Extraction (Claude tool use)

For each document, build a single tool call where the tool's input schema is the rule's Pydantic schema. Claude returns structured JSON conforming to the schema. Use Pydantic to validate; on validation failure, retry once with the validation error fed back in the prompt, then mark failed.

Pseudo-code (confirm against current Anthropic SDK):

```python
schema = load_rule(rule_id, version).pydantic_model
tool_def = {
    "name": "extract",
    "description": f"Extract fields from a {rule_id} document",
    "input_schema": schema.model_json_schema(),
}

response = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=4096,
    tools=[tool_def],
    tool_choice={"type": "tool", "name": "extract"},
    messages=[{"role": "user", "content": [
        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
        {"type": "text", "text": rule.extraction_prompt},
    ]}],
)
```

Always store the raw model response alongside the validated record. Useful for debugging and for re-running validation when schemas evolve.

## Chunking

For RAG, chunk each PDF at semantic boundaries (clause, section). Overlap 100 tokens. Each chunk row stores:

- `doc_id`, `chunk_index`
- `page_start`, `page_end`
- `char_start`, `char_end` (within the full extracted text)
- `text`
- `embedding` (vector)
- `rule_id`, `rule_version` (for traceability)

If a clean clause boundary cannot be detected (badly OCR'd, dense table), fall back to fixed-size chunks of ~1000 tokens with 100-token overlap.

## Idempotency

Files are identified by content hash, not path. Renaming or moving a file does not trigger reprocessing. Content change → new hash → new job. Job records prevent duplicate concurrent processing of the same hash.

The job table is the source of truth for processing state. Don't let workers do anything that isn't reflected in a job record.

## Job table shape

```
jobs (
    id uuid primary key,
    doc_id uuid,
    content_hash text not null,
    file_path text,
    rule_id text,
    rule_version text,
    status text not null,           -- pending, running, done, failed
    attempt_count int default 0,
    error_message text,
    created_at timestamptz default now(),
    started_at timestamptz,
    completed_at timestamptz
)
```

A worker claims a job by updating `status` from `pending` to `running` with row-level locking (`SELECT ... FOR UPDATE SKIP LOCKED`). Don't roll your own queue semantics on top of plain reads.

## What to defer

- SharePoint integration. Local watch folder is fine for POC. The watcher is a clean abstraction layer; SharePoint is a different implementation behind the same interface.
- Real OCR. Claude handles native PDF parsing; scanned-only documents fail at extraction with a clear error. Add OCR (Docling or Unstructured.io) only if dummy contracts include scanned ones.
- Re-extraction triggered automatically by rule version bumps. Build the data model to support it (`rule_id` and `rule_version` on every record). Trigger is manual.
