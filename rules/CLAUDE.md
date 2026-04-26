# Extraction rules

## What a rule is

A rule defines three things:

1. The Pydantic schema for positive extraction fields (parties, dates, payment terms, amounts).
2. The clause presence checklist (boolean flags for each clause type to look for).
3. The extraction prompt for Claude.

Each rule has a `rule_id` (stable identifier) and a `version` (semver). Files live at `rules/<rule_id>/<version>.yaml`. A `current.yaml` symlink or pointer file in each rule directory marks the active version.

## File format

```yaml
rule_id: saas_contract
version: 3.1.0
description: SaaS subscription contracts

extraction_prompt: |
  Extract contract fields per the schema. Be strict on dates: ISO 8601 only.
  If a field is genuinely absent, return null. Do not guess.
  For monetary amounts, capture the figure and the currency separately.

schema:
  parties:
    type: list[str]
    description: Legal entity names of the contracting parties
  effective_date:
    type: date
  expiry_date:
    type: date
  auto_renewal:
    type: bool
  payment_terms_days:
    type: int
    description: Net payment days, e.g. 30 for "net 30"
  annual_value:
    type: decimal
  currency:
    type: str
    description: ISO 4217 currency code, e.g. GBP, USD
  termination_for_convenience_notice_days:
    type: int | null

clause_checklist:
  has_dr_clause:
    description: Disaster recovery or business continuity obligations on the supplier
  has_data_residency_clause:
    description: Restrictions on where customer data may be stored or processed
  has_indemnity_cap:
    description: Cap on supplier liability for IP or third-party claims
  has_audit_rights:
    description: Customer right to audit supplier compliance
  has_change_of_control_clause:
    description: Provisions triggered by acquisition or material ownership change
```

The Pydantic model is generated from the `schema` block at load time. A hand-written model in `shared/models.py` mirrors it for IDE support, kept in sync via a test that asserts equivalence.

## Versioning policy

- **Patch (3.1.0 → 3.1.1).** Prompt clarification, no schema change. Existing records remain valid.
- **Minor (3.1.0 → 3.2.0).** Additive fields only. Older records have nulls for new fields. No re-extraction required.
- **Major (3.1.0 → 4.0.0).** Breaking schema change (renamed or removed fields, type changes, removed clause checks). Re-extraction job required.

The folder map (`rules/folder_map.yaml`) pins to a `rule_id`. The current major version is read from `rules/<rule_id>/current.yaml`. Patch and minor updates flow automatically; major bumps require an explicit pointer change and a re-extraction trigger.

## Adding a new rule

1. Create `rules/<rule_id>/1.0.0.yaml`.
2. Add a Pydantic model in `shared/models.py` matching the schema (the loader generates one dynamically; the manual model exists for IDE support).
3. Update `rules/folder_map.yaml` to point relevant folders.
4. Run the extraction CLI against three sample documents and inspect the output. Don't release into the watcher until the samples look right.
5. Add a fixture PDF to `tests/fixtures/contracts/<rule_id>/` and a test that asserts extraction shape.

## Modifying an existing rule

Decide which version increment applies (patch, minor, major). Write the new YAML alongside the old one. Update `current.yaml` to point at the new version. For a major version bump, also enqueue a re-extraction job for affected documents.

Old version YAML stays in the repo. Records extracted under the old version remain interpretable.

## What rules are not

- Not a query language. Rules define what gets extracted; queries operate on the result.
- Not user-editable in the POC. They're code, in git, reviewed.
- Not magic. If the source document doesn't contain the field, no rule recovers it.
- Not the right place to put business logic. "Flag contracts where renewal is within 90 days" is a query, not an extraction rule.

## Designing clause checks

The clause checklist is the commercially valuable part of the schema. A few principles:

- **Phrase as presence questions, not values.** `has_dr_clause: bool` is a clean check. `dr_clause_text: str` invites hallucination and makes negative-space queries harder.
- **Be specific in the description.** "Has DR clause" is too vague for Claude to evaluate consistently. "Disaster recovery or business continuity obligations on the supplier, including RTO/RPO commitments or tested recovery procedures" gives a concrete test.
- **Pair the boolean with an evidence field if needed.** `has_dr_clause: bool` plus `dr_clause_evidence: str | null` with a quote and page reference is the pattern when the legal team wants to verify findings.
- **Keep the list focused.** Ten well-defined checks beat thirty fuzzy ones. Add checks as the audit needs surface, not speculatively.
