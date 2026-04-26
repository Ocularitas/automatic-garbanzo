# Azure deployment — Cloud Shell runbook

Deploys Postgres + the contract-intelligence VM to Azure with one Bicep template
and a cloud-init bootstrap. Designed for Azure Cloud Shell (Bash) on a personal
free-tier subscription.

End state:
- Azure Database for PostgreSQL Flexible Server (B1ms, PG16, `vector` extension on)
- Linux VM (B2s, Ubuntu 24.04) running the ingestion watcher, query MCP server, and
  Caddy in front for HTTPS + bearer-token gating
- Public HTTPS endpoint at `https://<dnsLabel>.<region>.cloudapp.azure.com/mcp`

Approx wall time: 12–18 minutes including cloud-init.

---

## 1. Open Cloud Shell

Portal → top bar → `>_` icon → **Bash**. First run takes ~30s while it provisions a storage account. Skip if you've used Cloud Shell before.

## 2. Get the code into Cloud Shell

```bash
git clone --branch claude/review-claude-files-uaJD7 \
  https://github.com/Ocularitas/automatic-garbanzo.git
cd automatic-garbanzo
```

## 3. Generate an SSH key (skip if you already have one)

```bash
test -f ~/.ssh/id_ed25519 || ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519
```

## 4. Create the resource group

```bash
LOCATION=uksouth
RG=rg-contract-intel-poc
az group create -n $RG -l $LOCATION -o table
```

## 5. Mint the secrets

```bash
# Alphanumeric password — avoids URL-encoding pain in DATABASE_URL.
# Aa1 prefix guarantees the upper/lower/digit categories Azure PG requires.
PG_PASSWORD="Aa1$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 28)"
BEARER_TOKEN=$(openssl rand -hex 32)
echo "PG admin password: $PG_PASSWORD"
echo "MCP bearer token:  $BEARER_TOKEN"
```

> **Save those two values somewhere.** You'll need the bearer token to
> configure the Claude custom connector and the PG password if you ever
> want to connect with `psql`.

Set your API keys (you'll be prompted; pasted values won't echo):

```bash
read -rsp "Anthropic API key: " ANTHROPIC_KEY; echo
read -rsp "Voyage API key:    " VOYAGE_KEY;    echo
```

## 6. Deploy

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
  -o table
```

This blocks for ~8–12 minutes. PG Flexible Server is the slowest piece.

## 7. Read the outputs

```bash
DEPLOY=$(az deployment group list -g $RG --query "[0].name" -o tsv)
az deployment group show -g $RG -n $DEPLOY \
  --query 'properties.outputs.{mcpUrl:mcpUrl.value,sshCommand:sshCommand.value,pgFqdn:pgFqdn.value}' \
  -o table
```

You'll get back something like:

```
MCPUrl                                                              SSHCommand                                              PGFqdn
------------------------------------------------------------------  ------------------------------------------------------  ------------------------------------------
https://cipoc-abc123.uksouth.cloudapp.azure.com/mcp/                ssh azureuser@cipoc-abc123.uksouth.cloudapp.azure.com   cipoc-pg-abc123.postgres.database.azure.com
```

> The Claude connector URL is the **no-trailing-slash form** (e.g.
> `.../mcp`). FastMCP/uvicorn redirect `/mcp/` → `/mcp` internally, so use
> the canonical form to skip the redirect hop.

## 8. Wait for cloud-init to finish

The VM is reachable as soon as Bicep finishes, but cloud-init still has work to do
(install Caddy + uv, clone the repo, run migrations, start services). 3–5 more minutes.
Tail it:

```bash
SSH_CMD=$(az deployment group show -g $RG -n $DEPLOY \
  --query 'properties.outputs.sshCommand.value' -o tsv)
$SSH_CMD 'sudo cloud-init status --wait'
```

Expect `status: done`. If you see `status: error`, jump to the troubleshooting
section below.

## 9. Smoke-test the public endpoint

```bash
MCP_URL=$(az deployment group show -g $RG -n $DEPLOY \
  --query 'properties.outputs.mcpUrl.value' -o tsv)
BASE=${MCP_URL%/mcp}

# Healthcheck — no auth required.
curl -sS -i "$BASE/healthz"
# expect: HTTP/2 200, body "ok"

# MCP without auth — should be rejected.
curl -sS -i "$MCP_URL"
# expect: HTTP/2 401, body "Unauthorized"

# MCP with auth — should reach FastMCP.
curl -sS -i -H "Authorization: Bearer $BEARER_TOKEN" "$MCP_URL"
# expect: HTTP/2 200 or a valid MCP error (not 401)
```

The first request to a fresh hostname can take 30–60s while Caddy completes the
Let's Encrypt HTTP-01 challenge.

## 10. Add the connector in Claude

Settings → Connectors → Add custom connector:

- **Name:** Contract Intelligence (POC)
- **URL:** the `MCPUrl` value from step 7
- **Authentication:** API key / bearer
- **Header:** `Authorization: Bearer <BEARER_TOKEN>`

Then in any Claude chat: "List the contracts" or "What's in the corpus?" should
trigger the `list_contracts` tool. If the corpus is empty (we haven't ingested
anything yet), it'll return zero rows — that's the expected first state.

## 11. Drop in some PDFs

```bash
# from your laptop (not Cloud Shell — you have the SSH key locally)
scp ./your-saas-contract.pdf \
  azureuser@<fqdn>:/opt/contract-intel/data/watch/contracts/saas/
```

Within a few seconds the watcher creates a job; the worker processes it. Tail:

```bash
$SSH_CMD 'sudo journalctl -u contract-ingestion -f'
```

After the job logs as done:

```bash
$SSH_CMD 'sudo -u azureuser bash -lc "cd /opt/contract-intel/repo && \
  set -a && . /etc/contract-intel/env && set +a && \
  uv run python -c \"
from shared.db import session_scope
from sqlalchemy import text
with session_scope() as s:
    print(list(s.execute(text(\\\"select rule_id, parties, expiry_date from contracts\\\")).mappings()))
  \""'
```

Or just ask Claude: "How many contracts do we have? List them by expiry date."

---

## Troubleshooting

**`cloud-init status` reports `error`.** Read the log:
```bash
$SSH_CMD 'sudo cat /var/log/cloud-init-output.log | tail -100'
```

Most likely causes are repo clone failure (private repo? wrong branch?) or PG
firewall / credentials. Re-run the bootstrap idempotently:
```bash
$SSH_CMD 'sudo /opt/contract-intel/bootstrap.sh'
```

**Caddy fails to get a certificate.** Cloud-init's first run sometimes loses the
Let's Encrypt race if DNS hasn't propagated. Wait 60 seconds and:
```bash
$SSH_CMD 'sudo systemctl restart caddy && sudo journalctl -u caddy -n 100'
```

**`alembic upgrade head` fails with "extension vector is not allowed".**
The Bicep sets `azure.extensions=VECTOR` but PG sometimes needs a few minutes
to reload after the configuration change. Re-run `bootstrap.sh`.

**Want to redeploy from scratch.** Just `az group delete -n $RG --yes`. Everything
in this template lives in that one resource group.

**Where to change Caddy/systemd config.** It's all written by cloud-init from
`deploy/cloud-init.yaml`. To iterate live on the VM:
```bash
$SSH_CMD 'sudo nano /etc/caddy/Caddyfile && sudo systemctl reload caddy'
```
Just remember a re-deployment will overwrite local edits.

---

## Cost & lifecycle

While running: ~£35/month (B1ms PG ~£10 + B2s VM ~£25 + small egress).
Free trial credits ($200) cover this comfortably for the demo period.

To pause without losing data:
```bash
az vm deallocate -g $RG -n cipoc-vm
az postgres flexible-server stop -g $RG -n <pg-name>
```

To delete entirely:
```bash
az group delete -n $RG --yes
```
