# Production deployment topology

The target architecture for taking this system live in ABP. This document
is what the team's first sprint should consume; it's an input checklist,
not a finished design. Items in **bold ABP IT input** require a real
ABP-tenant decision before they can be implemented.

For the POC topology see `README.md` and `deploy/README.md`. For the
roadmap of changes between POC and production see `ROADMAP.md`. For the
design rules underlying these choices see `DECISIONS.md`.

---

## Target topology

Four parties, in the path of every request:

```
                        ┌──────────────────────────────────┐
                        │  Claude (user's Claude account)  │
                        │  Custom MCP connector            │
                        └────────────┬─────────────────────┘
                                     │ HTTPS, JWT bearer
                                     ▼
                ┌────────────────────────────────────────┐
                │  Azure API Management (APIM)           │
                │   - Public TLS termination             │
                │   - OAuth 2.1 flow with Entra          │
                │   - JWT validation per request         │
                │   - Rate limiting / throttling         │
                │   - Application Insights               │
                │   - Custom domain (e.g. mcp.abp.x)     │
                └────────────────┬───────────────────────┘
                                 │ private endpoint, JWT forwarded
                                 ▼
                ┌────────────────────────────────────────┐
                │  MCP server (FastMCP, contract-query)  │
                │   - Optional defence-in-depth JWT      │
                │     validation (MCP_OAUTH_* env)       │
                │   - /.well-known/oauth-protected-resource │
                │   - Tools serve evidence + URLs        │
                └────────────────────────────────────────┘

                ┌────────────────────────────────────────┐
                │  SharePoint (M365 tenant)              │
                │   - Source of contract PDFs            │
                │   - Bytes served direct to user's      │
                │     browser via M365 SSO               │
                │   - Audit / DLP / governance native    │
                └────────────────────────────────────────┘

                ┌────────────────────────────────────────┐
                │  Microsoft Entra (identity provider)   │
                │   - App registration for the MCP API   │
                │   - Pre-authorised Claude client       │
                │   - Issues JWTs                        │
                └────────────────────────────────────────┘
```

Three things to note:

1. **TLS terminates at APIM**, not at Caddy. Caddy goes away in production.
2. **Document bytes flow direct from SharePoint to the user**, not through
   APIM or the MCP server. The MCP returns `document_url` pointing at
   SharePoint; the user clicks; M365 SSO handles auth; SharePoint serves.
3. **Identity is JWT-claim-derived**, not hardcoded. The `user_id` /
   `group_id` columns in every table get populated from the validated
   token's claims at request time.

---

## Migration from POC

### What changes

| POC (today) | Production (target) |
|---|---|
| Caddy on Ubuntu VM, Let's Encrypt | APIM with custom domain (e.g. `mcp.abp.example`) |
| URL-embedded bearer in path | OAuth 2.1 flow + JWT bearer in `Authorization` header |
| Static shared secret | Entra-issued tokens, expiring, audited |
| `/<token>/docs/*` Caddy file-serves watch folder | SharePoint serves directly via M365 |
| Hardcoded `demo-user` / `demo-group` | JWT claims → `shared/identity.py` |
| No rate limit | APIM rate-limit policy |
| No central audit | APIM + Application Insights |

### What stays the same

- The MCP server's tool surface (`describe_schema`, `vector_search`,
  `query_contracts_structured`, `get_contract`, `list_contracts`,
  `get_clause_evidence`, `find_clause_gaps`)
- The data model (`documents`, `jobs`, `contracts`, `chunks`)
- The rule registry, the extraction pipeline, the embedding model
- The 44-test suite

### What's already in place to make the migration easy

The POC was built anticipating this migration. Specifically:

- **`shared/identity.py`** is the named swap point. Replace the demo
  identity function with one that reads JWT claims from the request
  context. No data-model changes needed.
- **`MCP_OAUTH_*` env vars** in `shared/config.py` enable JWT validation
  and the well-known metadata endpoint. Set them, restart the MCP server,
  done. Tests cover the gating; see `tests/test_oauth_wiring.py`.
- **`SourceLocator`** in `shared/urls.py` returns SharePoint URLs when
  present, falling back to the watch-folder form otherwise. Adding a
  `sharepoint_url` column populates this for free.
- **`PUBLIC_BASE_URL`** centralises where URLs point. Change once when
  the public domain moves from `*.cloudapp.azure.com/<token>` to
  `mcp.abp.example`.

These are listed as completed code hooks because they are. The team
doesn't need to write them.

---

## Required ABP IT decisions

Bold items must be settled before the team's first production sprint.

### 1. **Entra app registration**

Single app registration in the ABP tenant, identifying the MCP API:

- App name: `Contract Intelligence MCP` (or whatever)
- Sign-in audience: single tenant (ABP only)
- Application ID URI: `api://contract-intel-mcp` or similar
- **Scopes to define:** suggested `corpus.read`. If granular permissions
  are wanted (e.g. separate read for SaaS contracts vs leases), discuss
  before committing.
- Redirect URIs: Claude's connector callback URL (Anthropic publishes
  this; APIM may also need a redirect URI of its own)
- **Pre-authorise Claude as a known client.** Entra's native Dynamic
  Client Registration support is partial; pre-authorisation is the
  standard workaround. The Claude application's client_id is published
  by Anthropic.

### 2. **APIM resource provisioning**

A new APIM instance (or a tier in an existing one):

- **Tier decision.** Consumption tier is cheapest; Developer/Standard
  has SLA. Match ABP's existing API hosting patterns.
- Custom domain with ABP-managed TLS certificate (e.g.
  `mcp.contract-intel.abp.example`)
- OAuth 2.1 policy with PKCE pointing at the Entra tenant
- JWT validation policy on every backend request (validates audience,
  issuer, expiry, signature; reads scopes)
- Rate-limit policy: suggested 60 req/min/user as a starting point
- Throttle: suggested 1000 req/hour/user
- Application Insights instrumentation
- Reverse-proxy to the MCP server's private endpoint on the VM /
  Container Apps host

### 3. **MCP server hosting**

The current Ubuntu VM works; alternatives include:

- **Azure VM (current shape, easier migration)** — keep the existing
  systemd / uv setup. Move from public to private networking; APIM
  reaches it via VNet integration.
- **Azure Container Apps** — more cloud-native. Dockerise FastMCP.
  Watcher complications: needs a persistent file share for the watch
  folder, OR move to the SharePoint connector first.
- **Azure App Service** — straightforward but the file-watcher pattern
  fights deployments.

**Recommendation:** keep the VM short-term, plan migration to Container
Apps once the SharePoint connector replaces the watch folder.

### 4. **SharePoint connector**

The watch folder is a POC artefact. Production reads from SharePoint.

- **Source SharePoint sites identified.** Where do the contracts live?
  Single site, multiple? With which folder structure? This drives the
  rule folder map.
- **Service principal / managed identity** with Graph API read access:
  `Files.Read.All` (or scoped via Sites.Selected to specific sites for
  least privilege).
- **Mirror vs lazy fetch.** Recommendation: full mirror (download once
  on detection, ingest, store; embeddings need the bytes regardless).
  Re-embedding on every read would be wasteful.

### 5. **Page-anchor compatibility test**

Before making `document_url` SharePoint-pointing, confirm `#page=N`
works in ABP's M365 tenant configuration:

```
1. Drop any PDF in a SharePoint document library
2. Construct a share link ending in #page=3
3. Click from a fresh browser session
4. Observe whether the M365 viewer lands on page 3
```

Three outcomes:
- **Works** → ship as-is.
- **Doesn't work, but `#search=<quote>` works** → switch to search anchors.
- **Neither works** → surface page number prominently in response text
  alongside the document link. Functional but slightly worse UX.

### 6. **Real corpus assembly**

The POC ran on synthetic ABP-themed contracts. Production needs:

- **The actual contracts to ingest.** Which document set, in scope?
- **Quality checks** — scanned-only PDFs (no OCR text) will extract
  but won't be vector-searchable. Worth flagging during ingest.
- **Re-extraction policy** — `ingestion reextract` is manual today.
  When rules bump, who triggers? Probably the same team that approves
  rule PRs.

---

## Rate limiting / monitoring starting points

Suggested APIM policies for first production rollout. Tune from logs.

### Rate limits (per user)

```
60 requests / minute
1000 requests / hour
10000 requests / day
```

Rationale: a heavy chat session on the connector might hit `vector_search`
+ `get_contract` 4–6 times in a turn, with maybe 30 turns in a working
session. 60/min gives 10x headroom; 1000/hour is several full sessions;
10000/day is unbounded for normal use.

### Throttling on expensive tools

`reextract` (when the ops MCP lands) and any tool that triggers Claude /
Voyage API calls deserve their own throttles — those have direct cost
implications.

### Monitoring signals

- Request rate per tool name (Application Insights custom dimension)
- Error rate per tool name
- 99th-percentile latency per tool name
- JWT validation failures (likely indicates clock skew, expired tokens,
  or a misconfigured client)
- Anthropic / Voyage API errors at the ingestion service (separate
  Application Insights instance or shared)

---

## Operational concerns the team should plan for

### Re-extraction at scale

Today: 5 contracts, ~45 seconds total. Production: 500–5000 contracts.
A single rule bump could be a 30-minute synchronous re-extraction.
Options:

- **Background workers** — already supported (`ingestion worker` is a
  separate command). Run multiple replicas; the SKIP-LOCKED job claim
  handles concurrency.
- **Targeted re-extract** — `ingestion reextract --rule <id>` already
  exists. Bump a single rule, re-extract just that rule's files.
- **Cost ceiling** — each re-extraction costs Anthropic + Voyage tokens.
  Tracked via the `raw_response` JSONB on each contract (token counts
  visible in Anthropic's usage dashboard). Set a budget alert.

### Secrets management

Replace `/etc/contract-intel/env` with Azure Key Vault references for:

- `ANTHROPIC_API_KEY`
- `VOYAGE_API_KEY`
- `DATABASE_URL` (or use managed identity to PG)
- Anything that's currently a static token

The MCP-server bearer token disappears entirely once OAuth is in.

### Backup / restore

The POC has no backup story. Production:

- Postgres Flexible Server has built-in PITR backups (configure
  retention)
- The watch folder / SharePoint mirror needs a recovery plan if the
  VM disk goes
- The rules repo is git — that's the rule backup
- The `raw_response` JSONB on each contract row is the model-output
  archive. Don't lose it.

### Logging and audit

- APIM logs every request → Application Insights (centralised query,
  alerting)
- The MCP server logs structured JSON to journald → forward to
  Application Insights or Log Analytics via the Azure Monitor agent
- Postgres logs to its own log → can be exported
- SharePoint document opens are in M365 audit logs — that's the
  user-side audit trail and ABP IT already operates it

### Disaster recovery

Not a POC concern. For production: a second region, geo-redundant PG
backups, infrastructure-as-code (the Bicep template) deployable to a
second region in <1 hour. Probably overkill for v1; flag as a v2 item.

---

## What the team's first sprint should produce

Based on the above, a reasonable first-sprint scope:

1. APIM resource + Bicep module (Day 1–2)
2. Entra app registration + scope wiring (Day 1, parallel)
3. Cutover from URL-token to APIM-fronted JWT (Day 3)
4. Test with real Claude connector (Day 3)
5. Monitoring dashboard (Day 4)
6. Real corpus identified, SharePoint sites scoped (Day 4–5)
7. SharePoint connector implementation (sprint 2)
8. Page-anchor test (5 minutes once SharePoint connector lands)
9. Real-corpus ingest + sanity check (sprint 2)

Sprint 1 ends with: the system live on ABP infrastructure with real auth,
operating against the synthetic POC corpus. Sprint 2 swaps the corpus.

---

## What this document is not

- **Not a Bicep template.** A skeleton APIM module is on the ROADMAP as
  borderline-prep work; the team's actual APIM patterns will dictate
  what the final template looks like.
- **Not a final security review.** Anything touching real ABP
  commercial data needs ABP IT and legal sign-off independent of this.
- **Not a prescription.** Treat it as a default the team can override.
  Where a recommendation differs from ABP's existing patterns, the
  patterns win.
