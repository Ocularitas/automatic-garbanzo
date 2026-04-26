# Resume guide

Cheat sheet for picking the deployed Azure stack back up after Cloud Shell
times out, you've been away for a few days, or you've paused the resources
to save money. Assumes the deploy from `README.md` has already happened.

For first-time deployment, see `README.md`. For the system overview, see
`../ARCHITECTURE.md`.

---

## Reconnect to a fresh Cloud Shell

Cloud Shell drops your shell variables and (sometimes) your home directory
when sessions end. The Azure resources and the running VM are unaffected.

```bash
# 1. Re-clone if your home directory was reset
test -d ~/automatic-garbanzo || git clone --branch claude/review-claude-files-uaJD7 \
  https://github.com/Ocularitas/automatic-garbanzo.git
cd ~/automatic-garbanzo
git pull

# 2. Re-derive everything from the existing deployment
RG=rg-contract-intel-poc
DEPLOY=$(az deployment group list -g $RG --query "[0].name" -o tsv)
SSH_CMD=$(az deployment group show -g $RG -n $DEPLOY --query 'properties.outputs.sshCommand.value' -o tsv)
MCP_BASE=$(az deployment group show -g $RG -n $DEPLOY --query 'properties.outputs.mcpUrl.value' -o tsv)
FQDN=$(echo "$MCP_BASE" | awk -F/ '{print $3}')

# 3. Pull the bearer token (and other secrets) back from the VM
BEARER_TOKEN=$($SSH_CMD "sudo grep '^QUERY_MCP_BEARER_TOKEN=' /etc/contract-intel/env | cut -d= -f2-")

# 4. Reconstruct the connector URL
CONNECTOR_URL="https://${FQDN}/${BEARER_TOKEN}/mcp"
echo "Connector URL: $CONNECTOR_URL"
```

That's enough to interact with the deployment again. The rest of this
document is operations.

## Pause to save money, resume later

Stops compute billing without losing data. The VM and PG are deallocated
but the resource group and storage stay.

```bash
# Pause
PG_NAME=$(az postgres flexible-server list -g $RG --query "[0].name" -o tsv)
az vm deallocate                  -g $RG -n cipoc-vm    --no-wait
az postgres flexible-server stop  -g $RG -n $PG_NAME    --no-wait

# Resume (takes 2–5 minutes for PG to come back online)
az vm start                       -g $RG -n cipoc-vm    --no-wait
az postgres flexible-server start -g $RG -n $PG_NAME    --no-wait
```

After resume, give services a moment, then check they restarted on the VM:

```bash
$SSH_CMD 'systemctl is-active caddy contract-query-mcp contract-ingestion'
# expect three "active" lines
```

If `contract-ingestion` is `failed`, it usually means it tried to connect
to Postgres before PG was up. Just `restart`:

```bash
$SSH_CMD 'sudo systemctl restart contract-ingestion'
```

## Connect from your laptop (without using Cloud Shell)

The Cloud-Shell-generated SSH key never leaves Cloud Shell. To `scp` from
your laptop, generate a local keypair and authorize it on the VM.

On Windows (PowerShell):

```powershell
New-Item -ItemType Directory -Force -Path $HOME\.ssh | Out-Null
ssh-keygen -t ed25519 -f $HOME\.ssh\cipoc_key -N '""'
Get-Content $HOME\.ssh\cipoc_key.pub
```

On macOS / Linux:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/cipoc_key -N ''
cat ~/.ssh/cipoc_key.pub
```

Copy the public key, then in Cloud Shell:

```bash
PUBKEY='<paste the ssh-ed25519 ... line here>'
$SSH_CMD "echo '$PUBKEY' >> ~/.ssh/authorized_keys && echo OK"
```

From your laptop:

```bash
# macOS / Linux
scp -i ~/.ssh/cipoc_key ./contract.pdf azureuser@<FQDN>:/opt/contract-intel/data/watch/contracts/saas/

# Windows PowerShell
scp -i $HOME\.ssh\cipoc_key .\contract.pdf azureuser@<FQDN>:/opt/contract-intel/data/watch/contracts/saas/
```

For convenience, drop a `~/.ssh/config` entry:

```
Host cipoc
    HostName <FQDN>
    User azureuser
    IdentityFile ~/.ssh/cipoc_key
```

Then `scp contract.pdf cipoc:/opt/contract-intel/data/watch/contracts/saas/`.

## Pulling the latest code onto the VM

When you've pushed code changes to the branch and want them live:

```bash
# Idempotent: fetches, resets to origin, runs uv sync, runs migrations,
# (re-)enables systemd units. Doesn't reextract.
$SSH_CMD 'sudo /opt/contract-intel/bootstrap.sh'

# After a chunker change, rule version bump, or schema migration affecting
# extraction shape — re-process all PDFs.
$SSH_CMD 'sudo systemctl stop contract-ingestion && \
          sudo -u azureuser bash -lc "set -a && . /etc/contract-intel/env && set +a && cd /opt/contract-intel/repo && uv run ingestion reextract" && \
          sudo systemctl start contract-ingestion'

# Pick up MCP tool changes immediately (no need to wait for any natural restart)
$SSH_CMD 'sudo systemctl restart contract-query-mcp'
```

## Common day-2 operations

### Tail logs

```bash
$SSH_CMD 'sudo journalctl -u contract-ingestion -f'   # ingestion (Ctrl-C exits)
$SSH_CMD 'sudo journalctl -u contract-query-mcp -f'   # MCP server
$SSH_CMD 'sudo journalctl -u caddy -n 100 --no-pager' # last 100 caddy lines
```

### Inspect DB state

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
         ORDER BY c.created_at
    """)).mappings():
        path = r['file_path'].split('/')[-1]
        print(f"  {r['rule_id']:18s} {path:40s} {r['expiry_date']} {r['currency']} {r['annual_value']}")
    print('\n-- chunks per document --')
    for r in s.execute(text("""
        SELECT d.file_path, COUNT(c.id) AS n
          FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
         GROUP BY d.id, d.file_path ORDER BY d.file_path
    """)).mappings():
        print(f"  {r['file_path'].split('/')[-1]:40s} {r['n']} chunks")
PY
```

### Requeue failed jobs

```bash
$SSH_CMD 'sudo -u azureuser bash -lc "set -a && . /etc/contract-intel/env && set +a && cd /opt/contract-intel/repo && uv run python"' <<'PY'
from shared.db import session_scope
from ingestion.jobs import requeue_failed
with session_scope() as s:
    print(f"requeued {requeue_failed(s)} jobs")
PY
```

### Force a full re-extract

After a rule version bump, chunker change, or any time you want fresh
extractions for the whole corpus:

```bash
$SSH_CMD 'sudo systemctl stop contract-ingestion'
$SSH_CMD 'sudo -u azureuser bash -lc "set -a && . /etc/contract-intel/env && set +a && cd /opt/contract-intel/repo && uv run ingestion reextract"'
$SSH_CMD 'sudo systemctl start contract-ingestion'
```

`reextract` walks the watch folder synchronously, calls
`pipeline.process_file` per PDF, upserts on `(document_id, rule_id)`. The
`raw_response` JSONB on the contract row preserves the previous Anthropic
response on each upsert (overwritten with the new one).

### Rotate the bearer token

```bash
NEW_BEARER=$(openssl rand -hex 32)

$SSH_CMD "sudo bash -c '
  sed -i \"s|^QUERY_MCP_BEARER_TOKEN=.*|QUERY_MCP_BEARER_TOKEN=$NEW_BEARER|\" /etc/contract-intel/env
  sed -i -E \"s|Bearer [a-f0-9]{64}|Bearer $NEW_BEARER|; s|/[a-f0-9]{64}/mcp|/$NEW_BEARER/mcp|g\" /etc/caddy/Caddyfile
  caddy validate --config /etc/caddy/Caddyfile
  systemctl reload caddy
  systemctl restart contract-query-mcp
'"

echo "New connector URL: https://${FQDN}/${NEW_BEARER}/mcp"
BEARER_TOKEN=$NEW_BEARER
```

Update the connector URL in your Claude account settings to match.

### Rotate the Anthropic or Voyage API key

```bash
NEW_KEY=sk-ant-...   # or pa-... for Voyage
$SSH_CMD "sudo bash -c '
  sed -i \"s|^ANTHROPIC_API_KEY=.*|ANTHROPIC_API_KEY=$NEW_KEY|\" /etc/contract-intel/env
  systemctl restart contract-ingestion
'"
```

### Rotate the PG admin password

```bash
NEW_PG_PW="Aa1$(LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 28)"
PG_NAME=$(az postgres flexible-server list -g $RG --query "[0].name" -o tsv)
az postgres flexible-server update -g $RG -n $PG_NAME --admin-password "$NEW_PG_PW"

$SSH_CMD "sudo bash -c '
  sed -i -E \"s|(postgresql\\+psycopg://[^:]+):[^@]+@|\\1:$NEW_PG_PW@|\" /etc/contract-intel/env
  systemctl restart contract-ingestion contract-query-mcp
'"
```

## Tear down completely

```bash
az group delete -n $RG --yes
```

Removes the VM, the Postgres server, the public IP, the NSG, the VNet,
the resource group itself. Storage accounts inside the RG are also
removed. ~5 minutes wall time.

If you want to keep the data for export first:

```bash
PG_NAME=$(az postgres flexible-server list -g $RG --query "[0].name" -o tsv)
PG_FQDN=$(az postgres flexible-server show -g $RG -n $PG_NAME --query 'fullyQualifiedDomainName' -o tsv)
PG_USER=cipocadmin
read -rsp "PG admin password: " PG_PASSWORD; echo

PGPASSWORD="$PG_PASSWORD" pg_dump \
  -h "$PG_FQDN" -U "$PG_USER" -d contract_intel --no-owner \
  > contract_intel.dump.sql
```

That dump can be `psql`'d into any other Postgres 16 + pgvector instance —
including the eventual Mac mini.
