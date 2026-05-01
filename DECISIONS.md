# Design decisions

The rules-of-the-road for this codebase. Each decision below was a real fork
in the road; the section captures what we picked, why, and what it implies
for anyone extending the system. Read alongside `CLAUDE.md` (design intent)
and `ARCHITECTURE.md` (current shape).

When proposing a change that bumps against any of these, the right move is to
explicitly justify breaking the rule rather than to do it silently. If the
justification is good, update this document.

---

## 1. Source linkback is non-negotiable

**Decision.** Every chunk and every extracted field carries `doc_id`, `page`,
`char_start`, `char_end`, and a verbatim quote. No exceptions.

**Why.** The audience is procurement / legal / finance. The commercial
difference between this system and a free-text RAG tool is that every claim
the agent makes can be audit-trailed back to the source paragraph. Without
source linkback, the system is a guess engine; with it, it's evidence.

**Future work must.**
- Never introduce a result shape that loses the link back to the originating
  document and page.
- When adding a new rule field, populate `source_links[<field_name>]` for
  every value the model returns.
- When adding a new tool, return at minimum `document_id`, `file_path` (or
  `document_url` if `PUBLIC_BASE_URL` is set), and a page where one applies.

## 2. Rules are versioned Python modules; folder mapping is YAML

**Decision.** Rules live at `rules/<rule_id>/v<X_Y_Z>.py` as Python modules
that declare a `Fields` Pydantic class, a `Clauses` Pydantic class, an
extraction prompt, and a `RULE` registration. The active version is set by
the one-line `__init__.py` re-export. Old version files stay in the repo.
The folder-to-rule map (`rules/folder_map.yaml`) is YAML.

**Why.** The earlier design called for YAML rule files with a custom mini
type system (`type: int | null`, `type: list[str]`, etc.). We chose Python
modules instead so the schema *is* the Pydantic class — no dynamic-vs-handwritten
duplication, no parser, no runtime type system to maintain. YAML survives
only at the layer where it earns its keep: configuration that doesn't need
types (the folder map).

**Future work must.**
- Bump rule version per the patch / minor / major rules in `rules/CLAUDE.md`.
- Keep old version files in the repo so historical records remain interpretable.
- Never edit a published rule version in place when records exist under it
  (POC iteration aside — but commit message must call it out).
- Add new rule_ids to `KNOWN_RULES` in `rules/registry.py` and the
  `folder_map.yaml`.

## 3. One `contracts` table with promoted scalars + JSONB

**Decision.** A single `contracts` table holds extracted records for every
rule, with common scalar fields (`parties`, `effective_date`, `expiry_date`,
`currency`, `annual_value`) promoted to typed indexed columns and the rest
of the rule's structured payload in JSONB columns (`extracted`, `clauses`,
`source_links`, `raw_response`).

**Why.** The alternative — one table per rule — gives cleaner typing but
makes cross-rule queries (a key headline use case: "all contracts expiring
in Q2 regardless of type") harder, requires a migration for every new rule,
and forces tooling to know about each rule's schema separately. The promoted
columns give us typed filtering and ordering on the fields most queries
care about; the JSONB blobs give schema flexibility without migrations.

**Future work must.**
- Use the promoted columns for any filter / sort / typed read that needs
  precision. Read from `extracted` only when the field isn't promoted.
- Add new rule fields to `extracted` JSONB by default. Promote a field
  to a typed column only when (a) it's queried often, (b) cross-rule, and
  (c) the type is stable across rules. Promoting is a migration; do it
  deliberately.
- Preserve the `(document_id, rule_id)` unique constraint. Latest extraction
  wins per (doc, rule). `raw_response` JSONB is the audit trail for
  reconstructing prior versions if needed.

## 4. Boolean-with-evidence is the clause pattern

**Decision.** Every clause flag is a `has_X: bool` plus an optional
`has_X_evidence: str | None` for the verbatim quote. If the model sets the
flag `true`, the prompt requires it to populate (a) the matching evidence
field and (b) `source_links[<flag>]` with page + quote. A `true` without
evidence is wrong and should be rejected at review.

**Why.** Three alternatives were considered and rejected. (a) Free-text
clause fields invite hallucination and make negative-space queries harder.
(b) Typed objects with enums (e.g. `indemnity_cap: {basis: enum [...]}`)
lock in a taxonomy too early on a small corpus. (c) Boolean alone (no
evidence) makes audit defence impossible.

**Future work must.**
- Use the bool+evidence pattern for any new clause check.
- Phrase clause descriptions as presence questions ("Disaster recovery
  obligations including RTO/RPO commitments..."), not values. Specificity
  is the prompt's job, not the field name's.
- When a typed-object shape is genuinely warranted (typically: a
  commercially-meaningful structured value, e.g. an indemnity cap with
  multiplier), defer the typed shape until the corpus is large enough to
  defend the enum. Until then, capture the structure-as-text in the
  evidence field.

## 5. Jobs are idempotent and content-hash-keyed

**Decision.** The `jobs` table is the source of truth for processing state.
Workers claim jobs with `SELECT ... FOR UPDATE SKIP LOCKED`. Files are
deduplicated by SHA-256 content hash, not path. A partial unique index
(`status IN ('pending', 'running')`) prevents duplicate work on the same
hash; failed and done jobs don't block.

**Why.** This makes the watcher safe to re-run, multiple workers safe to
run concurrently, and renames / moves a no-op. The cost is one extra hash
read per file event; trivial.

**Future work must.**
- Never let workers do anything that isn't reflected in a job record.
- Failure must mark the job `failed` with an `error_message` — no
  fire-and-forget retries. Re-queueing is a deliberate operation.
- When adding a new ingestion phase, place the writes inside a single
  database transaction with the job-status update.

## 6. Auth is URL-embedded bearer for POC, APIM-fronted Entra OAuth for production

**Decision.** Two phases:

- **POC (today).** The MCP endpoint sits behind Caddy at
  `https://<fqdn>/<TOKEN>/mcp`, with a parallel `/<TOKEN>/docs/*` route for
  source-document delivery. The MCP server binds to localhost. A
  header-bearer alternative (`/mcp` with `Authorization: Bearer <token>`)
  exists for `curl` smoke tests.

- **Production (target).** Azure API Management is the public-facing
  gateway. APIM does the OAuth 2.1 flow with Entra, validates JWTs on
  every request, and applies rate-limiting / throttling / monitoring.
  FastMCP optionally also validates JWTs as defence-in-depth via the
  `MCP_OAUTH_*` env vars (`shared.config.Settings`, `_build_auth_provider`
  in `mcp_servers/query/server.py`). The `/.well-known/oauth-protected-resource`
  endpoint (RFC 9728) is exposed automatically when those env vars are
  set. TLS terminates at APIM. Source documents flow direct from
  SharePoint, not the gateway. The Caddy URL-token path is removed.

**Why.** Anthropic's custom-connector UI in `claude.ai` accepts only OAuth
or no auth — there's no header-bearer config. URL-embedded token is the
pragmatic POC stop-gap. APIM is Microsoft's reference architecture for
this exact scenario and matches the patterns ABP IT uses for other
internal APIs; choosing it lifts auth, governance, and observability
out of our codebase and into a managed service.

**Future work must.**
- During POC: treat the connector URL as a credential. Anyone with the URL
  has the whole corpus.
- When migrating to OAuth: remove the URL-token Caddy path and the
  header-bearer path together. Keep one auth model.
- Don't hardcode the POC token anywhere outside `/etc/contract-intel/env`
  and `/etc/caddy/Caddyfile`. The token rotation recipe in
  `deploy/RESUME.md` depends on this.
- Don't hardcode tenant ids, client ids, or scopes — they go through
  `MCP_OAUTH_*` env vars only. `deploy/PRODUCTION.md` is the runbook.

## 7. Document URLs: bare for document-level, page-anchored for clause-level; SharePoint as source-of-record in production

**Decision.** Every result row that references a document includes
`document_url`. For results that have a natural page (vector hits,
clause-evidence rows), the URL ends in `#page=N`. For document-level
results (list, structured filter, the get_contract envelope), `document_url`
is the bare URL. When the agent selects a clause flag in
`query_contracts_structured`, an additional `<flag>_source_url` is injected
with the page anchor. URLs are constructed via `shared.urls.build_document_url`
from a `SourceLocator` (file_path + optional sharepoint_url); if neither
form resolves, the URL key is omitted, not nulled.

**Why.** The whole point of source linkback is the user clicking through to
the actual paragraph. A bare document URL forces the reader to scroll; a
page-anchored URL closes the audit loop in one click. We omit rather than
null because a `null` looks like a deliberate "no source" rather than "no
URL configured."

In the production target, **SharePoint serves the bytes, not the gateway.**
Once the SharePoint connector is live, the documents table carries a
`sharepoint_url` per record and the URL helper resolves to it before
falling back to the watch-folder/Caddy form. Bytes never traverse APIM;
the user's M365 SSO session opens the document directly, with all of
SharePoint's existing audit / DLP / governance intact.

**Future work must.**
- When adding a new tool that returns documents or clauses, thread
  `document_url` through using the `_maybe_with_url` helper (or its
  equivalent for new shapes).
- Page anchors are the lowest-common-denominator that works in every
  browser. Don't ship features that require a custom PDF viewer.
  SharePoint's M365 viewer may not honour `#page=N` — see the
  page-anchor test in `ROADMAP.md` and `deploy/PRODUCTION.md`.
- When the URL helper returns None, omit URL keys silently — local dev
  and any document outside the addressable surface shouldn't see
  broken-link `null`s.
- Don't add a new pattern where the MCP tool returns document bytes
  inline. That collapses the audit trail, blows up context cost, and
  defeats SharePoint's native viewer. The MCP returns *evidence + a
  link*; SharePoint serves the bytes.

## 8. `describe_schema` is the orientation tool, and must stay live

**Decision.** `describe_schema` returns active rules with fields and clause
flags, the filter operator vocabulary, the persisted record envelope (how
the JSONB blobs are shaped on read, including the Decimal-serialization
convention), and corpus shape. Other tool descriptions point at it for
discovery. It's also exposed as MCP resources at `schema://rules`,
`schema://rules/{rule_id}`, `schema://corpus` for resource-aware clients.

**Why.** Without it, the agent learns the schema by sampling actual data —
wasteful in tokens and cycles, and breaks every time we bump a rule. With
it, the agent re-orients in one call. This is the cheapest possible answer
to "how does an agent stay current as the system evolves."

**Future work must.**
- Any new rule, field, clause flag, filter operator, or response-shape
  convention must show up in `describe_schema` automatically. The single
  source of truth is `_build_schema_payload` in `mcp_servers/query/server.py`
  — extend it there.
- Don't introduce tool surfaces with implicit conventions the agent has to
  learn by trial. If the agent has to call something to find out something,
  the answer goes here.
- Tests for describe_schema lock the contract in. Update them when the
  shape changes.

## 9. Select projection: three forms, explicit precedence, raise on unknown

**Decision.** `query_contracts_structured`'s `select` accepts three forms:
top-level field name, dotted JSONB path (`extracted.X`, `clauses.X`,
`source_links.X`), and bare leaf name resolved against `extracted` then
`clauses`. Top-level wins for collisions (e.g. `annual_value` exists both
as a promoted column and inside `extracted` — bare lookup returns the typed
column). Unknown selectors raise `ValueError` with the valid list inlined
in the message.

**Why.** The earlier silent-drop behaviour made `null` ambiguous: it could
mean "no value" or "you typed it wrong." That's the worst kind of bug —
the kind an LLM can't diagnose without a follow-up call. Loud failure plus
the inlined valid list closes the loop on the failure path without an
extra round trip.

**Future work must.**
- Never silently drop unrecognised input. Validate explicitly, raise with
  the valid set inlined.
- Preserve the precedence: top-level > extracted > clauses. The promoted
  column is the typed read; the JSONB is the schema-flexible read.
- When adding a new container (a hypothetical fourth JSONB column), add it
  to `JSONB_CONTAINERS` in `store.py` and to the `record_envelope` in
  `describe_schema`.

## 10. Tool descriptions are part of the API contract

**Decision.** The descriptions on `@mcp.tool()` decorators are user-facing
in the sense that Claude reads them when choosing a tool. They drive tool
selection, argument shapes, and the agent's mental model of the system.

**Why.** A tool with a vague description gets misused or ignored. A tool
with a precise description gets composed correctly. The cost of careful
descriptions is paid once; the cost of imprecise ones is paid on every
agent interaction.

**Future work must.**
- Treat description changes as API changes. Test agent behaviour against
  fixed prompts after non-trivial edits.
- Cross-reference between tools (e.g. "call `describe_schema` for the live
  list of clause flags"). Don't recite vocabulary that lives elsewhere.
- Examples in descriptions are worth more than abstract definitions for
  argument shapes.

## 11. Re-extraction is manual, idempotent, and triggered by the operator

**Decision.** Rule version bumps and prompt changes don't auto-trigger
re-extraction. The operator runs `uv run ingestion reextract` (or its
equivalent on the deployed VM) when ready. The pipeline upserts on
`(document_id, rule_id)` so the operation is safe to re-run; chunks are
replaced wholesale per document.

**Why.** Re-extraction costs Anthropic and Voyage tokens and can surface
new edge cases. Triggering it automatically on every rule change risks
silent regressions on the live corpus. Manual is slower; manual is also
safer and gives the operator a checkpoint to inspect.

**Future work must.**
- Don't add automatic re-extraction triggers. The trigger is a deliberate
  operator action.
- When introducing a change that requires re-extraction, say so explicitly
  in the commit message.
- A regression-diff before/after pass (planned, see `ROADMAP.md`) should
  be routine before merging any rule or prompt change.

## 12. Decimal-as-string in `extracted`; float in promoted columns

**Decision.** Pydantic `model_dump(mode="json")` serialises `Decimal` values
as strings to preserve precision; this is what lands in the `extracted`
JSONB blob. The promoted `annual_value` column is `Numeric(18,2)` and
returns as a float through the `_jsonable` helper. Same field, two types
depending on which path you read.

**Why.** The straightforward fix is to coerce Decimal-to-float during
serialisation, but that quietly trades precision for consistency on a
data type where precision is the whole point. Documenting the convention
is cheaper and more honest. The promoted column is the typed read; the
JSONB blob is the verbatim model output.

**Future work must.**
- Read promoted columns when the answer is "what's the annual value?"
- If reading from `extracted`, coerce explicitly. Don't rely on the value
  being a number.
- The convention is documented in `describe_schema().record_envelope`.
  Keep that text in sync if the convention ever changes.

---

## Cross-cutting principles

A few patterns repeat across the decisions above. If you find yourself
reaching for any of these, you're probably making the right call.

- **Loud over silent.** Validation failures raise with the valid set
  inlined. Unknown selectors don't drop. Missing source links omit, not
  null-pad.
- **Schema is data; data is schema.** `describe_schema` is the live
  description of every other tool. New conventions land there or they
  don't exist for the agent.
- **The audit trail is the moat.** Anything that breaks source linkback,
  weakens evidence requirements, or hides the per-version provenance is
  a regression.
- **Schema flexibility through JSONB; typed precision through promotion.**
  New fields land in JSONB. Promote when query patterns demand it.
- **Reversible decisions over expedient ones.** When in doubt, prefer the
  shape that lets a future iteration change its mind.
