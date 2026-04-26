# Azure deployment — Cloud Shell runbook

First-time deploy of the contract-intelligence stack to a personal Azure
subscription using Bicep + cloud-init. For day-2 operations (reconnecting,
pausing, resuming, log inspection), see `RESUME.md` in this directory.
For the system at a glance, see `../ARCHITECTURE.md`.

## What you'll end up with

- Azure Database for PostgreSQL Flexible Server (B1ms, PG16, `vector` extension).
- Linux VM (Ubuntu 24.04) running:
  - Caddy on :443 with auto-Let's-Encrypt TLS.
  - Query MCP server bound to 127.0.0.1:8765.
  - Ingestion watcher + worker (systemd).
- Public HTTPS endpoint at `https://<dns-label>.<region>.cloudapp.azure.com/<token>/mcp`
  — usable as a Claude custom-connector URL.

Wall time end-to-end: 15–25 minutes the first time. Most of it is waiting
for PG Flexible Server provisioning and cloud-init.

Ongoing cost while running: ~£35–80/month depending on which VM SKU you
end up on (see step 3). Free-trial / pay-as-you-go credits cover this.

## Prerequisites

- An Azure subscription **upgraded from Free Trial to Pay-As-You-Go**.
  Free Trial restricts the compute SKUs you can deploy. The upgrade is a
  no-cost portal action; trial credits remain valid.
- An Anthropic API key (`sk-ant-...`).
- A Voyage AI API key (`pa-...`) **with a payment method on file**. Without
  one, Voyage caps you at 3 requests/minute and ingestion will stall on the
  embedding step. Free token allowance still applies after adding a card.
- A device with PDFs you want to upload (your laptop, typically).

---

## 1. Open Cloud Shell

Portal → top bar → `>_` icon → **Bash**. First run takes ~30s. If you see
a `Microsoft.CloudShell` namespace warning, register it once:

```bash
az provider register --namespace Microsoft.CloudShell
```

## 2. Get the code into Cloud Shell

```bash
git clone --branch claude/review-claude-files-uaJD7 \
  https://github.com/Ocularitas/automatic-garbanzo.git
cd automatic-garbanzo
test -f ~/.ssh/id_ed25519 || ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519
```

## 3. Pick a VM SKU your subscription can actually deploy

This is where Free-Trial-upgraded accounts most often trip. Check what's
available in your target region:

```bash
LOCATION=uksouth
az vm list-skus -l $LOCATION --resource-type virtualMachines --all \
  --query "[?contains(name,'_B2') || contains(name,'_D2')].{Name:name, Restricted:restrictions[?type=='Location'].reasonCode | [0]}" \
  -o table | head -40
```

Anything with `Restricted: null` is openable. Pick the cheapest one.
Recommended order:

1. `Standard_B2s` (~£25/mo) — first choice if available. The default in the template.
2. `Standard_B2als_v2` (~£18/mo) — newer B-series; works with the unchanged template (still x86_64).
3. `Standard_D2lds_v6` (~£70/mo) — widely available on upgraded subs.

Note your choice as `VM_SIZE` for the deploy step:

```bash
VM_SIZE=Standard_B2s        # or whatever showed null Restricted
RG=rg-contract-intel-poc
az group create -n $RG -l $LOCATION -o table
```

## 4. Mint the secrets

```bash
# Alphanumeric password — avoids URL-encoding pain in DATABASE_URL.
# Aa1 prefix guarantees the upper/lower/digit categories Azure PG requires.
PG_PASSWORD="Aa1$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 28)"
BEARER_TOKEN=$(openssl rand -hex 32)

# API keys — pasted values won't echo
read -rsp "Anthropic API key: " ANTHROPIC_KEY; echo
read -rsp "Voyage API key:    " VOYAGE_KEY;    echo
```

> **Save `$BEARER_TOKEN` somewhere durable.** You'll need it to configure the
> Claude custom connector and to recover state in future Cloud Shell sessions.
> The PG password and API keys will also be pulled back from the VM's env
> file later if you need them.

## 5. Deploy

```bash
az deployment group create \
  --resource-group $RG \
  --template-file deploy/main.bicep \
  --parameters \
    sshPublicKey="$(cat ~/.ssh/id_ed25519.pub)" \
    pgAdminPassword="$PG_PASSWORD" \
    mcpBearerToken="$BEARER_TOKEN" \
    anthropicApiKey="$ANTHROPIC_KEY" \
    voyageApiKey="$VOYAGE_KEY" \
    vmSize="$VM_SIZE" \
  -o table
```

Blocks for ~8–12 minutes. PG Flexible Server is the slowest piece.

## 6. Read the outputs

```bash
DEPLOY=$(az deployment group list -g $RG --query "[0].name" -o tsv)
SSH_CMD=$(az deployment group show -g $RG -n $DEPLOY --query 'properties.outputs.sshCommand.value' -o tsv)
MCP_BASE=$(az deployment group show -g $RG -n $DEPLOY --query 'properties.outputs.mcpUrl.value' -o tsv)
FQDN=$(echo "$MCP_BASE" | awk -F/ '{print $3}')
echo "SSH:      $SSH_CMD"
echo "FQDN:     $FQDN"
echo "PG host:  $(az deployment group show -g $RG -n $DEPLOY --query 'properties.outputs.pgFqdn.value' -o tsv)"
```

## 7. Wait for cloud-init to finish

The VM is reachable as soon as Bicep finishes, but cloud-init still has 3–5
minutes of work (install Caddy + uv, clone repo, run migrations, start
services). Tail it:

```bash
$SSH_CMD 'sudo cloud-init status --wait'
# expect: status: done
```

If you see `status: error`, jump to **Troubleshooting** below.

## 8. Smoke-test the endpoints

```bash
# Healthcheck — no auth.
curl -sS -i "https://${FQDN}/healthz"
# expect: HTTP/2 200, body "ok"

# MCP without auth — rejected.
curl -sS -i "https://${FQDN}/mcp"
# expect: HTTP/2 401, body "Unauthorized"

# MCP with header bearer — for curl debugging only.
curl -sS -i -H "Authorization: Bearer $BEARER_TOKEN" "https://${FQDN}/mcp"
# expect: HTTP/2 406 with a JSON-RPC body and an mcp-session-id header

# MCP with URL-embedded token — what Claude actually uses.
CONNECTOR_URL="https://${FQDN}/${BEARER_TOKEN}/mcp"
curl -sS -i "$CONNECTOR_URL"
# expect: same 406 with JSON-RPC body
```

The 406 with `Client must accept text/event-stream` is the success signal —
it means auth passed and FastMCP is serving the request, but curl didn't
advertise SSE support. A real MCP client (Claude) handles that natively.

The first request to a fresh hostname can take 30–60s while Caddy
completes the Let's Encrypt HTTP-01 challenge. Retry once if you see a
TLS error on the first hit.

## 9. Add the connector in Claude

Settings → Connectors → Add custom connector:

- **Name:** Contract Intelligence (POC) — or whatever
- **URL:** `$CONNECTOR_URL` (the one ending `/<bearer_token>/mcp`)
- **Advanced settings:** leave OAuth Client ID and Secret **empty**

Claude probes the endpoint; on success the connector lists six tools
(`vector_search`, `query_contracts_structured`, `get_contract`,
`list_contracts`, `find_clause_gaps`, `get_clause_evidence`).

Quick sanity check in any chat: *"What tools do you have available from the
Contract Intelligence connector?"* — expect Claude to recite all six.

> Treat the connector URL as a credential. Anyone with the URL can read the
> corpus. Until phase 2 (Entra OAuth) lands, this is the security boundary.

## 10. Drop in some PDFs

Your private SSH key was generated inside Cloud Shell. To `scp` from your
laptop, either generate a new keypair locally and authorize it on the VM
(see `RESUME.md`), or upload PDFs into Cloud Shell first and `scp` from
there:

```bash
# Cloud Shell: upload PDFs via the toolbar's Upload icon, then:
scp ~/your-saas.pdf      azureuser@${FQDN}:/opt/contract-intel/data/watch/contracts/saas/
scp ~/your-services.pdf  azureuser@${FQDN}:/opt/contract-intel/data/watch/contracts/services/
scp ~/your-lease.pdf     azureuser@${FQDN}:/opt/contract-intel/data/watch/contracts/leases/
```

The folder under `contracts/` determines which rule applies. PDFs in any
other folder hit `generic_contract`. The watcher fires on file create and
modify; deduplication is by content hash, so a re-upload of the same file
is a no-op.

Watch ingestion progress:

```bash
$SSH_CMD 'sudo journalctl -u contract-ingestion -f'
# Ctrl-C when you see "done: N chunks" for each file
```

End-to-end per file: ~15–45 seconds for an average 20–30 page contract.
The Anthropic extraction call is the long pole.

Confirm rows landed:

```bash
$SSH_CMD 'sudo -u azureuser bash -lc "set -a && . /etc/contract-intel/env && set +a && cd /opt/contract-intel/repo && uv run python"' <<'PY'
from shared.db import session_scope
from sqlalchemy import text
with session_scope() as s:
    print('-- jobs by status --')
    for r in s.execute(text("SELECT status, COUNT(*) FROM jobs GROUP BY status")):
        print(r)
    print('\n-- contracts --')
    for r in s.execute(text("""
        SELECT c.rule_id, c.parties, c.expiry_date, c.currency, c.annual_value, d.file_path
          FROM contracts c JOIN documents d ON d.id = c.document_id
         ORDER BY d.file_path
    """)).mappings():
        path = r['file_path'].split('/')[-1]
        print(f"  {r['rule_id']:18s} {path:40s} parties={r['parties']} "
              f"expiry={r['expiry_date']} {r['currency']} {r['annual_value']}")
PY
```

Then back in Claude: *"How many contracts do we have? List them by expiry date."*
That should call `list_contracts` then pivot to `query_contracts_structured`
to sort by expiry. The conversation is the demo.

---

## Troubleshooting

### `cloud-init status` reports `error`

```bash
$SSH_CMD 'sudo cat /var/log/cloud-init-output.log | tail -100'
```

Most common causes:

- **Repo clone failure.** Make the repo public or pass a deploy key.
- **PG firewall / credentials.** Check `$SSH_CMD 'sudo cat /etc/contract-intel/env'` and try `psql $DATABASE_URL -c '\dt'`.
- **dpkg conffile collision on `caddy` install.** Already fixed in
  `cloud-init.yaml` (`Dpkg::Options::=--force-confold`); should not recur.

Re-run the bootstrap idempotently:

```bash
$SSH_CMD 'sudo /opt/contract-intel/bootstrap.sh'
```

### Caddy fails to get a certificate

DNS for the `*.cloudapp.azure.com` name sometimes hasn't propagated when
Caddy first tries the HTTP-01 challenge. Wait 60 seconds, then:

```bash
$SSH_CMD 'sudo systemctl restart caddy && sudo journalctl -u caddy -n 50'
```

Look for `certificate obtained successfully`.

### Voyage rate-limit errors during ingestion

`RateLimitError: You have not yet added your payment method...`

Add a payment method at the Voyage dashboard. Then requeue and let the
worker retry:

```bash
$SSH_CMD 'sudo -u azureuser bash -lc "set -a && . /etc/contract-intel/env && set +a && cd /opt/contract-intel/repo && uv run python"' <<'PY'
from shared.db import session_scope
from ingestion.jobs import requeue_failed
with session_scope() as s:
    print(f"requeued {requeue_failed(s)} jobs")
PY
```

### Anthropic / Voyage API key wasn't accepted

If the failures are Auth-related instead of rate-limit:

```bash
# Confirm the key on the VM
$SSH_CMD 'sudo grep -E "ANTHROPIC_API_KEY|VOYAGE_API_KEY" /etc/contract-intel/env'

# To rotate either key:
$SSH_CMD 'sudo bash -c "
  sed -i \"s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$NEW_KEY|\" /etc/contract-intel/env
  systemctl restart contract-ingestion contract-query-mcp
"'
```

### Want to redeploy from scratch

```bash
az group delete -n $RG --yes
```

Everything in this template lives in that one resource group. Then go back
to step 4 with new secrets.

### Where to change Caddy / systemd config

It's all written by cloud-init from `deploy/cloud-init.yaml`. Iterating
live on the VM:

```bash
$SSH_CMD 'sudo nano /etc/caddy/Caddyfile && sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy'
```

Local edits will be overwritten on the next `bootstrap.sh` run if the file
in the repo changes — push the change to git first and let the bootstrap
re-render it from the canonical source.

---

## Cost & lifecycle

Approx monthly cost while running:

| Item | Approx £/month |
|---|---|
| PG Flexible Server B1ms, 32 GB | ~£10 |
| VM B2s | ~£25 |
| VM B2als_v2 | ~£18 |
| VM D2lds_v6 | ~£70 |
| Public IP, networking, egress | ~£3 |

Free Pay-As-You-Go credits ($200) cover ~2–4 weeks of any of these.
Anthropic + Voyage costs for demo-scale work: pennies.

To pause without losing data, see `RESUME.md`.
