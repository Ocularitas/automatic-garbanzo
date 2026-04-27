# Architecture

What this system actually is, what it stores, how it talks to Claude, and what
you can change without breaking things. Read alongside `CLAUDE.md` (design
intent), `deploy/README.md` (first-time deploy), and `deploy/RESUME.md` (day-2 ops).

## What it does, in one paragraph

A folder watcher picks up new contract PDFs, sends each to Claude with a rule's
Pydantic schema as a tool, and writes the structured result + per-clause
evidence + vector chunks to Postgres. A separate process exposes that corpus
as seven MCP tools. The user's own Claude account is the front end: they ask
questions, Claude composes the tools.

## Components

```
                              ┌──────────────────────────┐
  PDFs scp'd into ─────►──────│  watch folder            │
  /opt/contract-intel/        │  /opt/contract-intel/    │
  data/watch/contracts/<rule>/│  data/watch              │
                              └────────────┬─────────────┘
                                           │ watchdog file events
                                           ▼
                              ┌──────────────────────────┐
                              │ ingestion service        │
                              │ (systemd: contract-      │
                              │  ingestion.service)      │
                              │                          │
                              │  watcher → hash → job    │
                              │  worker  → parse →       │
                              │           extract →      │
                              │           chunk → embed  │
                              └────┬─────────────────┬───┘
                                   │ writes          │ calls
                                   ▼                 ▼
                              ┌─────────┐   ┌─────────────────┐
                              │ Azure   │   │ Anthropic API   │
                              │ Postgres│   │ (PDF tool use)  │
                              │ + pgvec │   │                 │
                              └────┬────┘   │ Voyage API      │
                                   │ reads  │ (embeddings)    │
                                   ▼        └─────────────────┘
                              ┌──────────────────────────┐
                              │ query MCP server         │
                              │ (systemd: contract-      │
                              │  query-mcp.service)      │
                              │                          │
                              │  seven FastMCP tools     │
                              │  bound to 127.0.0.1:8765 │
                              └────────────┬─────────────┘
                                           │ proxied
                                           ▼
                              ┌──────────────────────────┐
                              │ Caddy (systemd: caddy)   │
                              │ TLS via Let's Encrypt    │
                              │ Auth: URL-embedded token │
                              │ public on :443           │
                              └────────────┬─────────────┘
                                           │ HTTPS POST
                                           ▼
                              ┌──────────────────────────┐
                              │ Claude (user's account)  │
                              │ Custom MCP connector     │
                              └──────────────────────────┘
```

All three systemd services run on the same Azure VM. The Postgres is a managed
Azure Database for PostgreSQL Flexible Server. They are decoupled — the
Postgres connection string is the only swap point if the VM moves (e.g. to a
Mac mini).

## Where each piece lives in the repo

| Component | Path | Notes |
|---|---|---|
| Pydantic models, DB row shapes, settings, identity | `shared/` | One source of truth for cross-service types. |
| Rules (Fields + Clauses + prompt + version) | `rules/<rule_id>/v*.py` | Versioned Python modules. `__init__.py` re-exports the active one. |
| Folder → rule mapping | `rules/folder_map.yaml` | The only YAML in the rules layer. |
| Ingestion pipeline (watcher, jobs, extract, chunk, embed, write) | `ingestion/` | Plus a CLI: `ingestion process`, `watch`, `worker`, `scan`, `reextract`. |
| Read-side store + MCP tool surface | `mcp_servers/query/` | `store.py` is testable SQL; `server.py` is the FastMCP wrapper. |
| Schema migrations | `db/migrations/versions/` | Alembic. Idempotent across re-runs. |
| Azure deployment artifacts | `deploy/` | `main.bicep`, `cloud-init.yaml`, `README.md`, `RESUME.md`. |

## Data model

Four tables, all in one Postgres database (`contract_intel`).

### `documents`

One row per *unique content* ever ingested. Keyed by `content_hash` (SHA-256
of the file bytes). The same PDF moved or renamed does not create a new row.

Notable columns: `rule_id`, `rule_version` (resolved at ingest time), plus
`user_id` and `group_id` for the eventual auth swap.

### `jobs`

Idempotent processing queue. Status enum: `pending` / `running` / `done` /
`failed`. Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED` so
multiple workers can run safely. A partial unique index
(`ix_jobs_pending_hash`) prevents two pending or running jobs for the same
content hash; failed and done jobs don't block.

### `contracts`

The structured-extraction landing table. **Option B from the design call:
one wide table with promoted scalar columns plus JSONB for rule-specific
fields, clause flags, and source links.** Rationale in `CLAUDE.md` and the
build conversation; in short, it makes cross-rule aggregation a normal SQL
query.

```
id              uuid pk
document_id     fk -> documents
rule_id, rule_version

-- promoted scalars (indexed; fast filtering and ordering)
parties         text[]
effective_date  date
expiry_date     date
currency        text
annual_value    numeric

-- everything else
extracted       jsonb   -- the full Fields model dump
clauses         jsonb   -- {has_dr_clause: true, has_dr_clause_evidence: "..."}
source_links    jsonb   -- {field_name: {page, char_start, char_end, quote}}
raw_response    jsonb   -- the full Anthropic response, for debugging / replay

user_id, group_id
created_at
```

Unique on `(document_id, rule_id)` (migration 0002). One current extraction
per (doc, rule). The `raw_response` JSONB preserves the model output if you
ever need to reconstruct an older extraction without re-billing the API.

### `chunks`

Vector store for RAG. Embedded via Voyage `voyage-3-large` (1024 dims).
HNSW index on the embedding column with cosine distance. Source linkback
(page + char offsets) on every row, per the non-negotiables.

Replaced in full each time a document is re-extracted (the writer issues
`DELETE FROM chunks WHERE document_id = ?` then bulk-inserts).

## Rule versioning, in operation

Rules are Python modules, not YAML. Each rule lives at:

```
rules/<rule_id>/
  __init__.py        # one-liner: `from .v3_2_0 import RULE`
  v3_2_0.py          # active version
  v3_1_0.py          # previous; stays in repo
```

Bumping the active version is editing the one-line `__init__.py`. Records
under the old version remain readable because the model class is still
importable from `v3_1_0.py`.

Versioning policy:

| Increment | Example | Effect | Re-extraction |
|---|---|---|---|
| Patch | 3.1.0 → 3.1.1 | Prompt clarification | Optional. Records remain valid. |
| Minor | 3.1.0 → 3.2.0 | Additive fields (new optional fields) | Recommended for the new fields to populate; old records have nulls. |
| Major | 3.1.0 → 4.0.0 | Breaking schema (renamed/removed/typed fields) | Required. |

Re-extraction is triggered manually with `uv run ingestion reextract`. The
pipeline upserts on `(document_id, rule_id)` so the operation is idempotent
and replaces the contract row in place.

## The seven MCP tools

Designed to compose. Claude (the agent in the user's chat) chooses; the user
asks a question. Each tool is orthogonal — no overlapping responsibility.

| Tool | When it's the right one |
|---|---|
| `describe_schema` | Orientation. Returns active rules + fields + clause flags + filter operators + record envelope + corpus shape in one call. The cheapest way for an agent to learn what's queryable, especially after a rule version bump. |
| `vector_search` | Discovery / RAG: "what does the corpus say about X?". |
| `query_contracts_structured` | Aggregation, filtered lists, anything where the answer is a SQL query over extracted fields. |
| `find_clause_gaps` | Negative space: "which contracts are missing X?" — directly maps to a `clauses.<flag>=false` filter. |
| `get_clause_evidence` | Inverse: "which contracts have X, and what does it say?" — single SQL pulling per-flag evidence + page. |
| `get_contract` | Full record for a single contract id, used after one of the search tools. |
| `list_contracts` | Browse, not analyse. "What's in here?". |

The query MCP also exposes three matching MCP **resources** —
`schema://rules`, `schema://rules/{rule_id}`, `schema://corpus` — backed
by the same data as `describe_schema` for clients that prefer the
resource channel.

The tool *descriptions* are user-facing in the sense that Claude reads them
when choosing a tool. They are part of the contract surface; treat changes
to them with the same care as changes to the schema.

## Auth model

**Phase 1 (now):** URL-embedded bearer token. The Caddy site config matches
`/<TOKEN>/mcp` and `/<TOKEN>/mcp/*`, strips the token prefix, and proxies
to `127.0.0.1:8765`. The MCP server itself is bound to localhost and has
no auth. The token lives in `/etc/contract-intel/env` and in
`/etc/caddy/Caddyfile`.

Why not a header bearer? Anthropic's custom-connector UI in claude.ai
currently only supports OAuth or no auth at the connector layer; arbitrary
headers aren't a config option. URL-embedded is the pragmatic stop-gap.

A second header-authenticated path (`/mcp` with `Authorization: Bearer …`)
is also configured for `curl` smoke tests. The tokens for both paths are
the same value.

**Phase 2 (planned):** OAuth via Microsoft Entra. FastMCP supports JWT
verification given a JWKS URI; advertising OAuth-protected-resource
metadata at `/.well-known/oauth-protected-resource` plus an Entra app
registration completes the flow. The Caddy URL-prefix gate would be
removed at the same time. This matches the production path described
in the root `CLAUDE.md`.

The non-negotiable `user_id` and `group_id` columns mean the auth swap
doesn't touch the data model — only the middleware that fills them in.

## Things that can change vs things that can't

**Cheap to change** (no re-extraction, no migration):
- Tool descriptions (the text in `mcp_servers/query/server.py`).
- The MCP server's tool *behaviour* (more filters, different return shapes), as long as the underlying SQL still works.
- The bearer token (rotate in `/etc/contract-intel/env` and `/etc/caddy/Caddyfile`, reload caddy, restart query MCP).
- Anthropic / Voyage API keys.
- Adding a new rule (new directory, register in `rules/registry.KNOWN_RULES`, add a folder_map entry). Existing contracts under other rules are unaffected.

**Cheap-ish** (single re-extraction pass, no migration):
- Chunker tuning (chunk size, overlap).
- A patch or minor rule version bump.
- A prompt change inside a rule.

**Schema migration** (Alembic):
- New rule fields that need to be promoted to scalar columns.
- New tables.
- Index changes on the contracts JSONB columns.

**Re-embedding** (re-extract everything, costs Voyage tokens):
- Changing `VOYAGE_EMBEDDING_MODEL` or `VOYAGE_EMBEDDING_DIMENSIONS`.
  The DB column dimension is fixed at migration time, so changing dimensions
  also requires a migration.

**Major effort** (new infra):
- Switching Postgres host (e.g. Azure → Mac mini): change `DATABASE_URL`,
  `pg_dump`/`pg_restore` if you want the data, re-run migrations on the new
  host, no code change.
- Switching auth from bearer to Entra OAuth: see Phase 2 above.

## What the system deliberately does not do

Deferred from the POC scope per the root `CLAUDE.md`. Listed here so it's
clear what you'd need to add for production use:

- Real auth. POC hardcodes a demo user/group via `shared.identity`.
- SharePoint integration. The watcher is a clean abstraction layer; SharePoint
  would be a different implementation behind the same interface.
- OCR for image-only PDFs. Claude's native PDF parsing handles most documents;
  scanned PDFs return empty text from pypdf, so vector search misses them but
  structured extraction can still succeed.
- Re-extraction triggered automatically on rule version bump. The data model
  supports it; the trigger is `ingestion reextract`, manual.
- Audit history of past extractions. The current row is the latest; older
  extractions are overwritten. `raw_response` JSONB is preserved for the
  current extraction only.
- Rule management UI. Rules are git-managed; phase 2 adds a separate MCP
  server for proposing rule changes via PR.
