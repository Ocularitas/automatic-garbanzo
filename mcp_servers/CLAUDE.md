# MCP servers

## Two servers, two audiences

- **query** server: read-only access to the contract corpus. Demo audience uses this.
- **rules** server: read and propose-change for extraction rules. IT and legal audience. Phase 2.

Keep them in separate processes with separate auth scopes. Don't merge.

## Tool design principles

The agent (Claude in the user's chat) composes tools. The user doesn't pick a tool; they ask a question and Claude chooses. So:

1. **Tools should be orthogonal**, not overlapping. One tool for structured queries, one for vector search, one to fetch a full document. No "smart" tool that internally branches based on the input shape.
2. **Tool descriptions are user-facing.** They drive Claude's tool selection. Write them carefully. Concrete examples in the description help.
3. **Return source linkback in every result.** Doc IDs, page numbers, chunk offsets. The agent surfaces them so users can navigate to the source.
4. **Pagination matters.** Aggregation queries can return hundreds of rows. Default page size 50, max 500. Include `has_more` and a cursor.
5. **Errors should be informative, not opaque.** Claude reads the error message and decides whether to retry or rephrase.

## Query server tools (initial set)

| Tool | Purpose |
|---|---|
| `vector_search` | Semantic retrieval across chunks. Args: `query`, optional `folder`, `top_k`. Returns chunks with doc_id, page, and snippet. |
| `query_contracts_structured` | Filter on extracted fields. Args: `filters` (dict of field → value or operator), `select` (fields to return), `limit`, `cursor`. Returns rows. |
| `get_contract` | Full record for one contract by ID, including all extracted fields and a source link. |
| `list_contracts` | Browse by folder or tag with optional filters. Lightweight; for "what's in here?" not for analysis. |
| `find_clause_gaps` | Given a clause name and a filter, return contracts where the presence flag is false. Convenience wrapper for negative-space queries. |

`find_clause_gaps` is technically expressible as a `query_contracts_structured` call, but giving Claude an explicit tool with the right name makes the negative-space pattern more discoverable and more testable.

## Tool description style

Bad:

> Search contracts.

Good:

> Run a structured query against extracted contract fields. Use this for aggregations and filtered lists, e.g. all contracts expiring in Q2, all SaaS contracts with payment terms over net 60. Returns rows with source links and supports cursor pagination.

The description is half the implementation. Test it by reading the descriptions and asking yourself whether you'd know which tool to pick for each of the three query patterns in the root CLAUDE.md.

## Rules server tools (phase 2, scaffold only for POC)

| Tool | Purpose |
|---|---|
| `list_rules` | Show all rules and their current versions. |
| `get_rule` | Full rule definition by `rule_id` and version. |
| `propose_rule_change` | Open a PR against the rules repo. Does not mutate live rules directly. |

Direct mutation is deliberately not exposed. Rule changes go through review even when proposed via the MCP.

## Auth

POC: bearer token in env var, hardcoded "demo_user". When Entra arrives, follow Microsoft's APIM + Entra reference architecture and swap the middleware. Don't build OAuth handling from scratch.

Reference: https://developer.microsoft.com/blog/claude-ready-secure-mcp-apim

## Transport

Streamable HTTP. SSE is deprecated as of early 2026.

## Local development and the demo

For the weekend POC: expose the query server via Tailscale Funnel from the Mac mini. Register as a custom connector in your personal Claude account (Settings → Connectors → Add custom connector). Demo from your account.

For org rollout: deploy behind APIM, register as an org-level custom connector via an org Owner. Out of scope for POC.

## Testing

The MCP server has two layers worth testing:

1. **Tool implementations.** Unit tests against the database. Standard.
2. **Agent behaviour with the tools.** Integration test: give Claude a fixed corpus, a fixed prompt, and assert which tools it calls. Catches description regressions. Use the Anthropic SDK directly; don't try to mock Claude.

Test fixtures live in `tests/fixtures/contracts/` as small synthetic PDFs.
