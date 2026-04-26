# Roadmap

Work that's been considered, designed at least at a sketch level, and
deliberately deferred. Each entry captures the motivation, scope, and any
prerequisites so the next pickup is a five-minute orientation.

When picking an item up, follow the rules in `DECISIONS.md`. Mark the item
done by removing it from this file (or moving to a "Shipped" appendix if
that becomes useful for retrospect).

---

## Near-term — high commercial value

### Multi-occurrence source links

**Why.** Real contracts discuss the same topic across multiple sections — a
headline indemnity cap in §11.2, carve-outs in §11.4, a definitional
cross-reference in the schedule. Today `source_links.<flag>` is a single
`{page, char_start, char_end, quote}` record, so only one occurrence
survives extraction. The agent and the user both lose the ability to ask
"where else does this come up?"

**Shape.** Augment, don't replace, so existing readers keep working.

```python
class SourceLinkOccurrence(BaseModel):
    page: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    quote: str | None = None

class FieldSourceLink(SourceLinkOccurrence):
    additional: list[SourceLinkOccurrence] | None = None
```

Top-level fields stay where they are (the primary occurrence). `additional`
is supplementary occurrences. JSONB-stored, no migration.

**Prompt rules** for what counts as a meaningful additional occurrence:
- Different wording, qualifications, exceptions, schedule entries.
- Not verbatim repetitions or simple cross-references like "as defined in §1.2".
- Empty / null when the clause is genuinely a one-page commitment.

**Response surface changes.**
- `get_contract.source_links.<field>.document_url` becomes a list when
  `additional` is populated.
- `get_clause_evidence` returns one row per contract with an
  `additional_occurrences: [{page, quote, document_url}]` list.
- `query_contracts_structured` keeps `<flag>_source_url` as the primary; a
  parallel `<flag>_source_urls` (plural list) for callers who want all.

**Prerequisites.**
- Regression-diff CLI (below) — the prompt change can shift how the
  primary occurrence is chosen, even though we're only adding the
  secondary.
- One re-extract pass after merge.

**Status.** Designed; deferred until after the regression-diff infrastructure
is in place.

### Regression-diff CLI

**Why.** The next ~3–5 changes on this roadmap are all prompt or schema
edits that affect how existing contracts get extracted. Without a
before/after diff per field, we ship blind. The original extraction
enhancement spec called this out; we agreed it earns its keep on the
first prompt change after multi-occurrence lands.

**Shape.** `uv run ingestion regression-diff [--rule rule_id]`:
1. Snapshot current contract rows and chunk counts to a JSON file.
2. Run `ingestion reextract` against the same corpus.
3. Read back, diff per field per contract, output a structured changelog
   (markdown table or JSON).
4. Highlight: fields that flipped, fields that newly populated, fields
   that became null.

**Out of scope for v1.** Diffing chunk text similarity, cost reporting,
embedding-distance changes — useful but not necessary for the first cut.

**Status.** Sketched; build before the next rule version bump that affects
existing data.

---

## Medium-term — extends what's there

### Ops MCP server (option B from the agency-vs-blast-radius discussion)

**Why.** Currently the operator drives day-2 ops via SSH (restart services,
requeue failed jobs, run reextract, tail logs). An ops MCP server alongside
the query one would let an authorised operator drive the same operations
from chat — within a strictly bounded surface, with audit logging, and
with explicit `confirm: bool` arguments on destructive operations.

**Shape.** A second FastMCP server at `mcp_servers/ops/`, on its own systemd
unit, on its own Caddy bearer-token path (`/<OPS_TOKEN>/ops`). Tools:

- `ingest_status()` — counts of jobs by status, last 5 errors
- `list_failed_jobs()` — fail history with paths and error messages
- `requeue_failed(job_id?)` — flip failures back to pending
- `reextract(rule_id?, confirm: bool)` — re-run pipeline (confirm required)
- `restart_service(name)` — only the three named services, whitelisted
- `tail_log(name, lines)` — read-only journalctl on whitelisted units
- `pull_latest_code(confirm: bool)` — git fetch + reset + bootstrap

**Why it matters as a design artifact.** Demonstrates "agentic ops within
constrained bounds" — important for procurement / IT audiences anxious
about what an AI agent can do to their infrastructure.

**Prerequisites.** None.

**Status.** Designed; deferred. Best built before any production deployment.

### Rules MCP server (Phase 2 from the original CLAUDE.md)

**Why.** The whole "Claude proposes a rule change" workflow is the natural
end-state of the agentic loop. Already specified in the root `CLAUDE.md`
as Phase 2.

**Shape.** A third FastMCP server, separate auth scope. Tools:

- `list_rules()` — all rules, current versions
- `get_rule(rule_id, version?)` — full definition
- `propose_rule_change(rule_id, intent, ...)` — opens a PR against the
  rules repo. Does **not** mutate live rules.

**Design note.** Prefer a structured-intent shape (e.g. `add_clause_check(
rule_id, flag_name, description, ...)`) over freeform code, so proposals
are grammatically constrained — the property you want when an agent is on
the other end. The PR review is the gate, the structured intent is the
brake.

**Prerequisites.** A "rule-author skill" in Claude Code is the natural
companion (see below).

**Status.** Specified in root CLAUDE.md; deferred until post-Mac-mini phase.

### Rule-author skill (Claude Code)

**Why.** Once the rules MCP exists, a Claude Code skill that takes
"add a clause check for X" and (a) drafts the rule diff, (b) calls
`propose_rule_change` to open the PR, (c) links the PR for human review
closes the natural-language → governed-change loop.

**Important.** The skill drafts and proposes; it does NOT merge. The merge
button is human. Don't collapse layers — that's the architectural moat.

**Prerequisites.** Rules MCP server.

**Status.** Sketched.

---

## Production-readiness

### Entra OAuth (replaces URL-embedded bearer)

**Why.** The URL-token model is fine for a closed POC but not for any
deployment touching real corporate data. The CLAUDE.md's Phase 2 production
path goes through Microsoft's APIM + Entra reference architecture.

**Shape.**
1. Register an Entra app: redirect URIs for the Claude connector callback.
2. FastMCP advertises OAuth-protected-resource metadata at
   `/.well-known/oauth-protected-resource` pointing at Entra.
3. FastMCP's `BearerAuthProvider` validates Entra-issued JWTs via the JWKS
   URI.
4. Caddy URL-token paths and the header-bearer path get removed in the
   same change.

**Prerequisites.** None technical, but: real auth implies real users,
which implies real `user_id` / `group_id` (the placeholder is in
`shared/identity.py`). Both swaps land together.

**Status.** Specified; deferred until Mac mini / production deployment.

### Decimal alignment between `extracted` and promoted columns

**Why.** Currently `annual_value` is `145000.0` (float) in the promoted
column and `"145000"` (string) in the JSONB blob. Documented as a known
convention in `describe_schema().record_envelope`, but it surprises every
caller who reads from both. Aligning means changing the writer to coerce
Decimal → float at JSONB serialisation time.

**Trade-off.** Float loses precision beyond what a 64-bit double can
represent. For monetary contracts under ~£10B that's never an issue;
for wholesale-finance-grade contracts it can be.

**Shape.** Either model-level Pydantic config (`ser_json_decimal="float"`
if Pydantic v2 supports it cleanly) or a custom serialiser in
`ingestion/writer.py` that walks the `extracted` dict before JSONB storage.
Re-extract pass to apply to existing rows.

**Status.** Documented as a convention; defer the alignment until either
(a) it actually bites someone, or (b) we accumulate a corpus that needs
the precision. Whichever comes first.

---

## Schema enhancements (waiting for more contracts)

### Typed indemnity cap (Phase 1 of the earlier extraction enhancement spec)

**Why.** Today `has_indemnity_cap` is a boolean plus an evidence string. A
structured shape (`{basis, multiplier, hard_amount, currency, direction,
losses_scope}`) would enable real cap-structure aggregation — the kind of
question procurement actually asks ("show me contracts with caps below 1×
annual fees").

**Why deferred.** The proposed `basis` enum (`fees_paid_total`,
`fees_paid_trailing_12m`, `contract_value`, `hard_amount`, `other`,
`unspecified`) commits us to a taxonomy at N=6. The 7th contract may not
fit. Defer until ~30 real contracts are in scope and the taxonomy is
defendable.

**Compromise sketch.** Capture the structure verbatim
(`basis_text: str`, `multiplier: float | null`, `hard_amount: number |
null`, `hard_amount_currency: str | null`) without the enum. Add the enum
mapping in a 3.4.x patch when the corpus warrants it.

**Status.** Designed; deferred for taxonomy validation.

### `quality_warnings` as a SQL view + MCP tool

**Why.** Phase 3 of the earlier spec. Surface extraction-quality issues
that the strict prompt should prevent but might not catch: a `true` flag
without an `*_evidence` field, money populated with null currency, a
boolean object with `present: true` but a missing source quote.

**Shape.** A SQL view `v_quality_warnings` computed on read (not stored —
keeps automatically in sync with rule schemas). An MCP tool
`list_extraction_warnings()` returns rows: contract id, rule, warning
type, evidence.

**Why a view, not a column.** Computing on read means no migration, no
write path that can drift, and no requirement to re-extract every time we
add a new warning rule.

**Status.** Designed; defer until typed-indemnity-cap (above) lands —
quality validation matters more once the schema has richer structure to
validate.

### Mirror data-protection cluster onto `services_contract`

**Why.** Phase 2 of the earlier spec landed on `saas_contract` 3.3.0 with
five new clause flags (`has_dpa_reference`,
`has_international_transfer_mechanism`, `has_sub_processor_controls`,
`has_security_certifications`, `has_data_return_clause`) plus two scalars.
Some of these (DPA reference, data return) apply to any data-touching
service contract too.

**Shape.** Bump `services_contract` 1.0.0 → 1.1.0 with the relevant subset.
Skip cert / transfer mechanism / sub-processor controls if they're rare
in services contracts; keep DPA reference and data return.

**Prerequisites.** None.

**Status.** Considered; defer until you've ingested a few non-SaaS
data-touching service contracts and can see what's actually present.

---

## Notes for future iteration

A pattern worth respecting: when this list grows past ~15 items, prune.
Items that have been deferred for more than ~3 months are usually either
(a) actually unimportant, in which case delete them, or (b) genuinely
blocked, in which case the blocker is the interesting thing to track here,
not the original idea.

When the multi-occurrence work lands, consider this file the input to a
"first decision the next iteration sees" — anyone returning to the project
should be able to read `DECISIONS.md` + `ROADMAP.md` and have the full
picture in fifteen minutes.
