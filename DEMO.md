# Demo script

A repeatable script for showing the system to a new audience (board,
customer, IT, legal). Each section has the question to ask, the tool
sequence to expect, the answer shape, and what specifically to draw
attention to.

The script also doubles as a regression check: if you run through it
end-to-end after a rule or prompt change, anything that suddenly
behaves differently is a signal for the regression-diff workflow.

---

## Pre-flight (60 seconds)

Before the audience joins:

1. **Verify the system is reachable.**
   ```bash
   curl -sS -i "https://${FQDN}/healthz"   # 200 ok
   ```
   If this fails, see `deploy/RESUME.md`.

2. **Verify the connector in your Claude account** lists the seven
   tools. Open a chat and ask:
   > *"What tools do you have available from the Contract Intelligence
   > connector?"*

   Expect a concise list: `describe_schema`, `vector_search`,
   `query_contracts_structured`, `get_contract`, `list_contracts`,
   `find_clause_gaps`, `get_clause_evidence`. If only some appear,
   reconnect the connector (Claude caches the tool list per
   connection).

3. **Have a fresh chat ready.** Don't reuse a chat that already has
   tool-call history; it can shortcut composition you want to
   demonstrate.

---

## Act 1 — Orientation

### Q1. *"What tools do you have available, and what's currently in the corpus?"*

**Expected.** Claude calls `describe_schema` once. The response covers
rules, fields, clause flags, filter operators, the record envelope, and
corpus shape — all in one call.

**Reply shape.** A summary of the seven tools, the four active rules
(`saas_contract` 3.3.0, `services_contract` 1.0.0, `lease`,
`generic_contract`), and the corpus contents (5 contracts: 3 SaaS, 2
services).

**Point to make.** *"The agent didn't have to fish for this. One tool
call, comprehensive answer. That's how an agent stays current as the
schema evolves."*

---

## Act 2 — Aggregation across contracts

### Q2. *"List every contract by counterparty, expiry date, and annual value."*

**Expected.** `list_contracts` or `query_contracts_structured` (either
works for the simple form). A clean table.

**Reply shape.** Five rows, sorted reasonably:

| Counterparty | Type | Annual value | Expiry |
|---|---|---|---|
| Brightwave Cloud Solutions | SaaS (ERP) | GBP 480k | Sep 2027 |
| Helio People Systems | SaaS (HR) | USD 145k | Dec 2026 |
| PortSight Analytics | SaaS (logistics) | GBP 78k | Aug 2026 |
| Marston & Hale | Services (legal) | GBP 180k | Mar 2027 |
| Northforge Industrial | Services (steel supply) | GBP 425k | Jun 2026 |

**Point to make.** *"Five contracts; one of them is in dollars. That
mixed-currency aggregation is structured query territory — a free-text
RAG tool would compute the wrong total. Structured fields enable
real numeric answers."*

### Q3. *"Which contracts expire in the next 12 months? Sort by expiry."*

**Expected.** `query_contracts_structured` with a date filter
(`expiry_date <= 2027-05-01` or similar).

**Reply shape.** Three contracts in order: Northforge (Jun 2026),
PortSight (Aug 2026), Helio (Dec 2026). The two 2027 expiries are
excluded.

**Point to make.** *"Renewal calendar in one query. Procurement asks
this every quarter; this gives them a self-serve answer with citations."*

---

## Act 3 — Negative space (the commercial heart)

### Q4. *"Which SaaS contracts don't have a disaster recovery clause?"*

**Expected.** `find_clause_gaps(clause_flag="has_dr_clause", rule_id="saas_contract")`.

**Reply shape.** Returns the SaaS contracts where `has_dr_clause = false`.
Whichever contracts are flagged as absent.

**Point to make.** *"This is the question that's hard for free-text
search and easy for us. Every SaaS contract has been audited for DR
language at ingest time; the absence is queryable. Negative-space
questions are the commercial moat versus tools that only know what's
written, not what's missing."*

### Q5. *"Which SaaS contracts lack a Data Processing Agreement reference?"*

**Expected.** `find_clause_gaps(clause_flag="has_dpa_reference", rule_id="saas_contract")`.

**Point to make.** *"GDPR audit question. Same shape as the DR
question. This is the kind of board-pack-feeding query that used to
take a paralegal a week."*

---

## Act 4 — Evidence with citation

### Q6. *"What does each SaaS contract say about indemnity caps? Cite the source."*

**Expected.** `get_clause_evidence(clause_flag="has_indemnity_cap", rule_id="saas_contract")`.

**Reply shape.** Per-contract: the verbatim quote from the cap clause,
the page number, and a clickable `document_url` ending in `#page=N`.

**Point to make.** *"Two things to notice. First, the agent is
quoting the contract directly — no paraphrase that could lose meaning.
Second, the link goes to the right page in the source PDF."*

→ **Click one of the links.** The PDF should open in the browser,
jumped to the cited page. *"That's the audit trail closed. Two-line
answer in chat, one-click verification in the source document, no
paralegal in between."*

### Q7. *"For each SaaS contract, give me the data-breach notification window in hours, with a deep link to the source."*

**Expected.** `query_contracts_structured` with `select=
["data_breach_notification_window_hours", "has_data_breach_notification"]`.

**Reply shape.** Three rows: Helio at 72 hours (with a
`has_data_breach_notification_source_url` ending in `#page=2`),
Brightwave and PortSight as null / silent.

**Point to make.** *"This is a numeric scalar lifted out of the
extraction. The 72 hours isn't paraphrased — it's an integer in our
structured field. We can compute on it: average, distribution,
contracts under 24 hours, etc. The deep link is per-clause, anchored
to the page where the clause lives."*

---

## Act 5 — Discovery / RAG

### Q8. *"What does the corpus say about audit rights?"*

**Expected.** `vector_search(query="audit rights")` likely as the
first call, possibly followed by `get_clause_evidence` if the agent
recognises this is a structured-flag question (`has_audit_rights`).

**Reply shape.** A summary across the contracts that have audit
rights, with quotes and page numbers.

**Point to make.** *"Discovery questions where the answer is in the
prose, not a structured field, fall through to vector search. Same
citation pattern; same deep links. The agent picks the right tool
based on the question shape — composition is its job, not the user's."*

---

## Act 6 (optional) — The "fix it live" moment

This is the cute one. Use sparingly; it lands harder if it follows
genuine surprise rather than rehearsed setup.

### Trigger. *Audience asks something the current schema can't answer cleanly.*

E.g. *"Does any contract have a most-favoured-nation clause?"* — we
don't have a `has_mfn_clause` flag. Claude will fall back to vector
search and might surface mentions but won't cleanly answer.

### Move. **Pivot to Claude Code (a separate window).**

> *"The schema doesn't have an MFN flag. Watch — let me ask the
> developer agent to add one."*

In Claude Code:
1. *"Add an MFN clause check to the saas_contract rule. Bump to a
   minor version. Include evidence."*
2. The agent edits `rules/saas_contract/v3_X_0.py`, updates
   `__init__.py`, runs tests, commits.
3. On the deployed VM:
   ```bash
   $SSH_CMD 'sudo /opt/contract-intel/bootstrap.sh && \
             sudo systemctl stop contract-ingestion && \
             sudo -u azureuser bash -lc "cd /opt/contract-intel/repo && \
                set -a && . /etc/contract-intel/env && set +a && \
                uv run ingestion reextract" && \
             sudo systemctl start contract-ingestion && \
             sudo systemctl restart contract-query-mcp'
   ```
4. Back in the demo chat: re-ask the question. Now answered.

**Point to make.** *"That's not a stunt. The change went through git,
through code review, through a versioned rule, through re-extraction,
and is logged. Procurement doesn't need to wait six months for a
software vendor to add a feature; the system is the feature, and the
schema is owned by us. That's what 'rules in code' actually means."*

**Caveat.** Don't do this if the demo timeline is tight. The cycle
is ~3-5 minutes. If something fails (test fails, rule schema
incompatible with old records), the live recovery is awkward.
**Pre-rehearse this exact flow.** The regression-diff CLI is your
friend here:

```bash
$SSH_CMD 'sudo -u azureuser bash -lc "cd /opt/contract-intel/repo && \
   set -a && . /etc/contract-intel/env && set +a && \
   uv run ingestion regression-diff --rule saas_contract"'
```

— gives you a markdown summary of what the rule change actually moved.

---

## Closing

After the demo, the natural questions:

- **"Where does this go from here?"** Point at `ROADMAP.md`. The
  multi-occurrence source links, the typed indemnity cap, the SharePoint
  connector are all real next-step items, not vapourware.
- **"How does this become production?"** Point at `deploy/PRODUCTION.md`.
  APIM, Entra OAuth, SharePoint, monitoring — the migration path is
  concrete.
- **"What about a different document type?"** Adding a new rule is
  ~30 minutes plus re-extraction. Walk through `rules/CLAUDE.md` if
  there's appetite.
- **"How do you trust the extractions?"** Two answers: every
  positive flag has a verbatim quote and a page link; every prompt
  change runs through `regression-diff` before merge. The audit trail
  is the moat.

---

## Pre-demo checklist

Run through this 10 minutes before any live demo. Each takes a few
seconds.

- [ ] `curl https://${FQDN}/healthz` returns 200
- [ ] `uv run health-check` from Cloud Shell returns ok=true
  (verifies DB + Anthropic + Voyage from outside the VM)
- [ ] Connector in the demo Claude account lists all seven tools
- [ ] Sample question Q1 answers correctly without a tool error
- [ ] Sample question Q6's first link clicks through to the right
  page in the PDF
- [ ] Browser is signed into the right Claude account (you don't want
  to demo "let me just sign in real quick")
- [ ] Cloud Shell tab open with `$SSH_CMD` set, in case live debugging
  is needed
- [ ] Don't run the live "fix it" demo (Act 6) cold — pre-rehearse

---

## What this script is not

- **Not a sales deck.** No screenshots, no logos, no positioning. The
  point is the working system; the deck goes around it.
- **Not exhaustive.** Eight questions covers ~80% of the system's
  surface. Cherry-pick whichever fit the audience's interests.
- **Not static.** When a rule bump or new tool changes the
  expected answer to a question, update the script. A demo script
  whose expected answers don't match reality is worse than no script.
