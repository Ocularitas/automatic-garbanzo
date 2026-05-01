# Lessons

The pain points we hit while building this POC, written up as
**symptom / cause / fix** so the team doesn't re-discover them. Each
entry references the relevant code or doc where the long-form fix lives.

If you hit something not listed here that took you more than 30 minutes
to figure out, please add it. This document earns its keep one entry at
a time.

---

## Azure / deployment

### "SkuNotAvailable" on `az deployment group create`

**Symptom.** Bicep deploy fails with
`{"code": "SkuNotAvailable"} ... Standard_B2s ... is currently not available
in location 'uksouth'`.

**Cause.** Azure Free Trial subscriptions restrict most compute SKUs.
Even after upgrading to Pay-As-You-Go some older B-series and D-series
SKUs stay restricted; only certain v6 generations open up.

**Fix.** Don't fight it. Run:

```bash
az vm list-skus -l uksouth --resource-type virtualMachines --all \
  --query "[?starts_with(name,'Standard_B2') || starts_with(name,'Standard_D2')].{Name:name, Restricted:restrictions[?type=='Location'].reasonCode | [0]}" \
  -o table
```

Pick anything with `Restricted: null`. `Standard_D2lds_v6` is the
reliable Intel-x86_64 fallback (~£70/month). Pass it via
`--parameters vmSize=Standard_D2lds_v6`. See `deploy/README.md` step 3.

### "extension X is not allow-listed"

**Symptom.** Alembic migration fails with
`extension "pgcrypto" is not allow-listed for users in Azure Database for PostgreSQL`.

**Cause.** Azure PG Flexible Server gates which Postgres extensions can
be installed. `vector` is allow-listable via the `azure.extensions`
server parameter (the Bicep does this); `pgcrypto` isn't, on most tiers,
without a paid support escalation.

**Fix.** Don't use `pgcrypto`. `gen_random_uuid()` is in core Postgres
since 13 — no extension needed. The migration in
`db/migrations/versions/0001_initial.py` already drops the pgcrypto
`CREATE EXTENSION` call.

### Cloud-init aborts during `apt install caddy`

**Symptom.** `dpkg-preconfigure: unable to re-open stdin` followed by
`Sub-process /usr/bin/dpkg returned an error code (1)`. Cloud-init
status reports `error`. Caddy is half-installed, `systemctl restart
caddy` fails, and subsequent bootstrap steps (uv sync, alembic) never
ran.

**Cause.** Cloud-init's `write_files` puts `/etc/caddy/Caddyfile` on
disk *before* the caddy package install runs. dpkg detects a
user-modified conffile, prompts interactively, and dies because
cloud-init has no stdin.

**Fix.** Already in `deploy/cloud-init.yaml`: the apt install passes
`Dpkg::Options::=--force-confold` so dpkg silently keeps our Caddyfile.
If you hit this on an existing VM, recovery recipe is in
`deploy/RESUME.md` (force-remove caddy with
`--force-remove-reinstreq`, reinstall with `--force-confnew`, restore
the Caddyfile, re-run bootstrap).

### "TLS connect error" after Caddy reload

**Symptom.** `curl: (35) TLS connect error: error:0A000438:SSL
routines::tlsv1 alert internal error` against the public hostname.

**Cause (most likely).** Caddy's site config is pointing at a hostname
Let's Encrypt can't validate. We hit this when a recovery script
tried to derive the FQDN from `hostname -f` (which returns the
internal `cipoc-vm`, not the public `*.cloudapp.azure.com` name).

**Fix.** Always render the Caddyfile from a known-good source: either
`deploy/cloud-init.yaml` template via the rebuild script in
`deploy/RESUME.md`, or pass the FQDN explicitly. Never grep it out of
the existing Caddyfile.

### Cloud Shell session ended; lost shell variables

**Symptom.** `$RG`, `$DEPLOY`, `$BEARER_TOKEN`, etc. are gone.

**Cause.** Cloud Shell sessions time out (~20 min idle) or close when
the browser tab does. Shell variables are not persisted; only files
under `~/` survive (and only if the same fileshare is mounted on next
launch).

**Fix.** Re-derive everything in 30 seconds. The recipe is at the top
of `deploy/RESUME.md`:

```bash
RG=rg-contract-intel-poc
DEPLOY=$(az deployment group list -g $RG --query "[0].name" -o tsv)
SSH_CMD=$(az deployment group show -g $RG -n $DEPLOY \
  --query 'properties.outputs.sshCommand.value' -o tsv)
BEARER_TOKEN=$($SSH_CMD "sudo grep '^QUERY_MCP_BEARER_TOKEN=' \
  /etc/contract-intel/env | cut -d= -f2-")
```

The VM is fine. Only your shell state is gone.

---

## Auth / connectors

### Claude custom-connector UI rejects bearer header config

**Symptom.** `claude.ai` Settings → Connectors → Add custom connector
shows OAuth Client ID / Secret fields under Advanced, with no field for
`Authorization: Bearer <token>`.

**Cause.** Anthropic's UI implements MCP's OAuth 2.1 spec for connector
auth. There is no header-bearer config option.

**Fix.** Two options:

1. **POC pattern (current):** put the bearer token in the URL path
   itself: `https://<host>/<TOKEN>/mcp`. Caddy validates the path
   prefix; FastMCP stays auth-naive on localhost. Leave OAuth fields
   empty in the connector UI. See `DECISIONS.md` decision #6 and
   `deploy/cloud-init.yaml`.
2. **Production pattern:** real OAuth 2.1 + Entra. Code hooks already
   in place via `MCP_OAUTH_*` env vars. See `deploy/PRODUCTION.md`.

### Claude returns 307 redirect on `/mcp/`

**Symptom.** `curl https://<host>/<token>/mcp/` returns
`HTTP/2 307 location: /mcp` — the request worked but redirected.

**Cause.** FastMCP / uvicorn auto-redirect `/mcp/` (with trailing
slash) → `/mcp` (without). The Bicep originally output the URL with a
trailing slash, which produced a redirect on every request.

**Fix.** Use the no-trailing-slash form. The Bicep now outputs the
correct shape; the Caddy `path` matcher accepts both forms. If you
hand-craft a connector URL, end it in `/mcp` (or `/<token>/mcp`).

### "Unauthorized" 401 on `/mcp` with the right token

**Symptom.** `curl -i https://<host>/<token>/mcp` returns
`401 Unauthorized` even though the token is current.

**Cause (commonly).** The token in the URL contains characters Caddy's
matcher treats specially, OR you're using the *old* token after a
rotation, OR Caddy's reload didn't pick up the new Caddyfile.

**Fix.** Tokens are 64 hex chars (`openssl rand -hex 32`) — no
URL-special characters. Verify:

```bash
$SSH_CMD 'sudo grep "^QUERY_MCP_BEARER_TOKEN" /etc/contract-intel/env'
$SSH_CMD 'sudo grep "Bearer " /etc/caddy/Caddyfile'
```

Both should match. If they don't, follow the rotate-bearer recipe in
`deploy/RESUME.md`.

---

## Ingestion

### Voyage rate-limit errors during first ingest

**Symptom.** Failed jobs with
`RateLimitError: You have not yet added your payment method in the billing page and will have reduced rate limits of 3 RPM and 10K TPM`.

**Cause.** Voyage's free tier (no payment method on file) is capped at
3 requests per minute. A bulk scp of more than 3 PDFs blows this
within seconds; jobs fail in batches.

**Fix.** Add a payment method at https://dashboard.voyageai.com → Billing.
Standard rate limits apply within ~2 minutes; the 200M-token free
allowance still applies. Then requeue:

```bash
$SSH_CMD 'sudo -u azureuser bash -lc "
  cd /opt/contract-intel/repo &&
  set -a && . /etc/contract-intel/env && set +a &&
  uv run python"' <<'PY'
from shared.db import session_scope
from ingestion.jobs import requeue_failed
with session_scope() as s:
    print(f"requeued {requeue_failed(s)} jobs")
PY
```

### Ghost jobs with `content_hash = e3b0c44...`

**Symptom.** Job table contains pending jobs with the SHA-256 of the
empty string as the hash. They never process and they cause
`requeue_failed` to fail with a unique-index conflict.

**Cause.** `scp` creates the target file as zero bytes *before*
streaming the data into it. The watchdog `on_created` event fires at
that instant; we hash the (empty) file, get `e3b0c44...`, enqueue a
job for content that doesn't exist.

**Fix.** Already patched: `ingestion/watcher.py` skips zero-byte
files; the modified-event later catches the populated content. If you
have ghost jobs from before the patch:

```sql
DELETE FROM jobs WHERE content_hash = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
```

Then `requeue_failed` works as expected.

### `requeue_failed` raises a unique-index conflict

**Symptom.** `psycopg.errors.UniqueViolation: duplicate key value
violates unique constraint "ix_jobs_pending_hash"`.

**Cause.** The partial unique index allows only one pending-or-running
job per content hash. When you flip a failed job back to pending, if
another row with the same hash is already pending, the constraint
fires.

**Fix.** Already patched: `ingestion/jobs.py:requeue_failed` deletes
failed rows that share a hash with a still-pending sibling before the
UPDATE.

---

## Database

### Decimal values look different in `extracted` vs the promoted column

**Symptom.** `get_contract` returns `annual_value: 145000.0` at the
top level but `extracted.annual_value: "145000"` inside the JSONB
blob. Same field, different type.

**Cause.** Pydantic's `model_dump(mode="json")` serializes `Decimal`
as a string to preserve precision. The promoted column is `Numeric`,
which our `_jsonable` helper coerces to `float`.

**Fix.** Documented in `describe_schema().record_envelope`. Read the
promoted column for typed numeric reads; coerce explicitly when
reading from `extracted`. The convention is by design — see decision
#12 in `DECISIONS.md`.

### Re-extraction creates duplicate contract rows

**Symptom (early build only).** After a rule version bump, `list_contracts`
shows two rows per contract — one at the old version, one at the new.

**Cause.** Original schema had `(document_id, rule_id, rule_version)`
as the unique constraint, so a 3.2.0 row and a 3.3.0 row coexisted.

**Fix.** Migration `0002_unique_contracts_per_rule` dropped that
constraint and replaced it with `(document_id, rule_id)`. Latest
extraction wins per (doc, rule); the audit trail of past extractions
lives in `raw_response` on the current row.

---

## Local dev

### `cat /etc/contract-intel/env: No such file or directory`

**Symptom.** Self-explanatory.

**Cause.** That file lives on the deployed VM, not on your laptop or
Cloud Shell.

**Fix.** Wrap in `$SSH_CMD`:

```bash
$SSH_CMD 'sudo cat /etc/contract-intel/env'
```

Same for `systemctl is-active`, `journalctl -u`, etc. — they all need
the SSH wrapper to reach the VM.

### Bash quoting hell with nested heredocs

**Symptom.** `SyntaxError: invalid syntax. Perhaps you forgot a comma?`
on a Python f-string passed via `$SSH_CMD '...'`.

**Cause.** Single-quoted Bash strings still expand single-quoted
substrings inside them, producing surprising results when you try to
nest `'...'` inside `'...'`.

**Fix.** Pipe Python source via heredoc to a remote `uv run python`
reading stdin:

```bash
$SSH_CMD 'sudo -u azureuser bash -lc "set -a && . /etc/contract-intel/env && set +a && cd /opt/contract-intel/repo && uv run python"' <<'PY'
# real python here, no quoting fights
print("hi")
PY
```

The `<<'PY'` (single-quoted delimiter) preserves the body verbatim.

---

## Adding to this list

If you've spent more than 30 minutes diagnosing something — especially
something the current docs don't surface — please add an entry. Format:

- One-line symptom (what you'd see)
- One-paragraph cause (why)
- Concrete fix (commands, code reference, or doc pointer)

Keep entries terse. Long entries that need explanation belong in
`ARCHITECTURE.md` or `DECISIONS.md`; this file is reference material.
