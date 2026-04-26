# Contract Intelligence POC

## What this is

A document intelligence tool for contracts. Watches a folder, extracts structured fields using the Claude API with versioned rules, indexes both structured data and document chunks for retrieval, and exposes the corpus through MCP servers so users query it from inside their Claude (no custom front end).

Target: working demo by Monday. Demo audience: commercial, procurement, finance, legal.

## Architecture

```
SharePoint or local folder
        │
        ▼
   Ingestion service ──────────► Postgres (pgvector + structured tables)
                                          ▲
                                          │
                            MCP query server (read)
                            MCP rules server (read + propose, phase 2)
                                          ▲
                                          │
                                       Claude
```

Three deployable components, one shared datastore:

- **Ingestion service.** Watches folder, queues new and changed files, runs extraction workers. No user surface.
- **Query MCP server.** Exposes search, retrieval, and structured-query tools. Connected to Claude as a custom connector.
- **Rules MCP server.** Separate connector, separate auth scope. Phase 2.
- **Datastore.** Postgres 16 with pgvector. Structured tables for extracted fields, vector table for chunks. One database, two storage patterns.

## Three query patterns, one chat

The user experience is a single conversation in their Claude. Internally, three patterns:

| Pattern | Example | Tool that serves it |
|---|---|---|
| Single-doc Q&A | "When does the SAP contract expire?" | `vector_search` then `get_contract` |
| Aggregation | "Table of all contract expiry dates" | `query_contracts_structured` |
| Negative space | "Which SaaS contracts are missing DR clauses?" | `query_contracts_structured` filtering on `has_dr_clause = false` |
| Discovery / RAG | "What does our corpus say about indemnity caps?" | `vector_search` |

Claude (the agent) chooses tools. Tools are designed to compose, not to route on pre-classified query type.

## Non-negotiables

These lock you in if missed. Skipping them later means reprocessing the corpus or rebuilding the pipeline.

1. **Source linkback metadata** on every chunk and every extracted field. Each row carries `doc_id`, `page`, `char_start`, `char_end` (or bbox for chunks).
2. **Versioned rule sets.** Each extraction record carries `rule_id` and `rule_version`. Rules live in git as YAML; the ingestion service reads the current version and stamps it.
3. **Idempotent extraction queue.** Job records with `status` (pending / running / done / failed) and `attempt_count`. Workers pick up pending jobs; failures are retryable. No fire-and-forget.
4. **User and group columns on every table** even though the POC has no real auth. `user_id` and `group_id` default to a hardcoded demo user. When Entra arrives, swap the auth middleware; tables don't change.
5. **Clause-presence flags** in the extraction schema, not just positive fields. `has_dr_clause: bool`, `has_termination_for_convenience: bool`, etc. against a configurable checklist. Negative-space queries become SQL filters, not RAG fishing expeditions. This is where the system earns its keep commercially.

## Deliberate scope cuts (POC)

- No real auth. Hardcoded demo user with full permissions. Auth middleware is in place, ready to swap.
- No SharePoint connector. Local watch folder, populated manually.
- No production deployment. Runs on Mac mini M4, exposed via Tailscale Funnel for the demo.
- No rules management UI. Rules are YAML files in git; edit via Claude Code or any editor.
- Dummy contracts only. No real ABP commercial documents until auth and hosting are real.
- No re-extraction trigger on rule version bumps. The data model supports it; the trigger is manual.

## Tech stack

- Python 3.12, `uv` for dependency management
- Postgres 16 with pgvector, in Docker Compose
- FastAPI for any HTTP surfaces
- FastMCP for MCP servers
- Claude API for extraction (tool use with Pydantic schemas) and chat (agent)
- Claude native PDF support for parsing, no separate OCR layer
- Pydantic v2 for all schemas
- pytest for tests

Embedding model: pick one and fix it in `.env`. Changing the model means re-embedding the corpus.

## Directory layout

```
contract-intelligence/
├── CLAUDE.md
├── docker-compose.yml
├── .env.example
├── pyproject.toml
├── README.md
├── ingestion/        ← see ingestion/CLAUDE.md
├── mcp_servers/      ← see mcp_servers/CLAUDE.md
│   ├── query/
│   └── rules/
├── rules/            ← see rules/CLAUDE.md
├── db/
│   └── migrations/
├── shared/
│   └── models.py     ← Pydantic schemas shared across services
└── tests/
```

## Working principles for Claude Code in this repo

- Schema first. Define Pydantic models before writing pipelines or handlers.
- Tests for the extraction layer. The rest can wait until POC works end-to-end.
- One commit per logical change. Branches if you're trying something speculative.
- When in doubt, prefer reversible decisions. The non-negotiables above are the irreversible ones; everything else can be rewritten.
- Don't introduce a framework or library without flagging it. Stack choices above are deliberate.
- If a design question is ambiguous, ask before assuming. Better to pause than to ship a wrong-shape decision into the irreversible layer.
