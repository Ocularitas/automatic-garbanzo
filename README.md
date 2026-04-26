# Contract Intelligence POC

Document intelligence for contracts. See `CLAUDE.md` for architecture and design notes.

## Quickstart (local)

```bash
# 1. Install deps
uv sync

# 2. Start Postgres + pgvector
docker compose up -d

# 3. Apply migrations
uv run alembic upgrade head

# 4. Configure
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY and VOYAGE_API_KEY

# 5. Process a single document (one-shot, useful for demo prep)
uv run ingestion process ./data/watch/contracts/saas/example.pdf

# 6. Or run the watcher
uv run ingestion watch

# 7. Run the query MCP server
uv run query-mcp
```

The query MCP server speaks streamable HTTP on `http://localhost:8765/mcp` by default. Add it as a custom connector in your Claude account.

## Layout

```
contract-intelligence/
├── ingestion/        # Watcher, queue, extraction, chunking, embedding
├── mcp_servers/
│   ├── query/        # Read-only query tools (POC)
│   └── rules/        # Rule management (phase 2)
├── rules/            # Versioned rule modules + folder_map.yaml
├── shared/           # Pydantic models shared across services
├── db/migrations/    # Alembic migrations
└── tests/
```

## Production path

The POC runs locally against Docker Compose Postgres. To move to Azure:

1. Provision Azure Database for PostgreSQL Flexible Server with the `vector` extension enabled.
2. Set `DATABASE_URL` to the Azure connection string.
3. Re-run `alembic upgrade head` against the new instance.

Code does not change. The connection string is the only swap point.
