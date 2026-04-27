# Contract Intelligence POC

Document intelligence for contracts. A folder watcher picks up new PDFs,
extracts structured fields and clause-presence flags via Claude (with
versioned per-rule schemas), embeds the text for semantic search, and
exposes the corpus through an MCP server so users query it from inside
their own Claude account — no custom front end.

Target audience: commercial, procurement, finance, legal teams who need
contracts queryable by typed fields *and* by clause language, with
clickable citations back to the originating page.

## Repository map

```
contract-intelligence/
├── CLAUDE.md           # Original design intent (the brief)
├── ARCHITECTURE.md     # Current shape: components, data model, tools
├── DECISIONS.md        # Design rules — what to preserve when extending
├── ROADMAP.md          # Deferred work with sketches and prerequisites
├── README.md           # You are here
│
├── ingestion/          # Watcher, jobs, extraction, chunking, embedding
├── mcp_servers/
│   └── query/          # Read-only query tools (the seven MCP tools)
├── rules/              # Versioned rule modules + folder_map.yaml
├── shared/             # Pydantic models, settings, identity, urls, db
├── db/migrations/      # Alembic migrations
├── deploy/             # Azure deployment artifacts
│   ├── main.bicep      # Single-file IaC: PG Flexible Server + VM + networking
│   ├── cloud-init.yaml # VM bootstrap: uv, Caddy, systemd, repo clone
│   ├── README.md       # First-time deploy runbook (Cloud Shell)
│   └── RESUME.md       # Day-2 ops: reconnect, pause, resume, rotate, tear-down
├── docker-compose.yml  # Local Postgres + pgvector for dev
└── tests/              # 44 unit tests (no DB required)
```

## What's actually here today

- **Four rules** active: `saas_contract` (3.3.0, with indemnity carve-outs
  and a data-protection cluster), `services_contract` (1.0.0), `lease`
  (1.0.0), `generic_contract` (1.0.0 fallback).
- **Seven MCP tools** on the query server: `describe_schema`,
  `vector_search`, `query_contracts_structured`, `get_contract`,
  `list_contracts`, `get_clause_evidence`, `find_clause_gaps`. Plus three
  matching `schema://` resources.
- **Source linkback** on every result via clickable `document_url` (and
  page-anchored `<flag>_source_url` when a clause flag is selected) when
  the deployment serves PDFs at the configured `PUBLIC_BASE_URL`.
- **End-to-end deployment** to Azure via Bicep: PG Flexible Server +
  Ubuntu VM + Caddy + systemd, gated by a URL-embedded bearer token
  consumable as a Claude custom connector.

For what's *not* here yet (and why), see `ROADMAP.md`.

## Quickstart — local dev

```bash
# 1. Install deps
uv sync

# 2. Start Postgres + pgvector locally
docker compose up -d

# 3. Apply migrations
DATABASE_URL=postgresql+psycopg://contract:contract@localhost:5432/contract_intel \
  uv run alembic upgrade head

# 4. Configure
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and VOYAGE_API_KEY at minimum

# 5. Process a single document (one-shot)
uv run ingestion process ./data/watch/contracts/saas/example.pdf

# 6. Or run the watcher + worker
uv run ingestion watch

# 7. Run the query MCP server
uv run query-mcp
```

The query MCP server speaks streamable HTTP on
`http://localhost:8765/mcp` by default. For local dev with Claude Code or
a CLI client, that's enough. For the chat UI, you need a public HTTPS
endpoint — see the Azure deploy.

## Quickstart — Azure deploy

The full runbook is `deploy/README.md`. One Bicep template, one
cloud-init script, ~15–25 minutes wall time end-to-end. Produces:

- Azure Database for PostgreSQL Flexible Server with `vector` extension
- Linux VM (Ubuntu 24.04) running ingestion + query MCP behind Caddy
- Public HTTPS endpoint at
  `https://<dns-label>.<region>.cloudapp.azure.com/<bearer-token>/mcp`,
  consumable as a Claude custom-connector URL
- A parallel `/<token>/docs/*` route serving the source PDFs so chat
  citations deep-link to the right page

Approximate cost: £35–80/month while running, depending on the VM SKU
your subscription permits. Pause / resume / tear-down recipes are in
`deploy/RESUME.md`.

## Tests

```bash
uv run pytest
```

44 tests covering rule registry, schema validation, chunker, select
projection, URL helper, and `describe_schema` payload shape. None require
a database or external API keys — they exercise pure logic. Integration
testing of the extraction layer is intentionally manual (it costs real
Anthropic tokens).

## Pointers

- **Want to use the system?** Read `deploy/README.md` and `deploy/RESUME.md`.
- **Want to understand how it's built?** Read `ARCHITECTURE.md`.
- **Want to extend it?** Read `DECISIONS.md` first — it captures the rules
  of the road. Then `ROADMAP.md` for what's already on the deferred list.
- **Want to add a rule or tweak an existing one?** Read `rules/CLAUDE.md`.
- **Want to operate the running system?** Read `deploy/RESUME.md`.

## Status

Personal POC. The CLAUDE.md (root) describes the deliberate scope cuts:
no real auth (hardcoded demo user, swappable), no SharePoint integration
(local watch folder), no production deployment hardening. Designed so
each cut becomes a known swap point rather than a rebuild.
