# Extraction rules

> Note: this doc supersedes the original YAML-driven design. We chose to define
> rules as Python modules so the schema *is* a Pydantic class (no custom
> mini-type-system, no dynamic-vs-handwritten model duplication). The
> versioning, clause-checklist, and folder-map ideas are unchanged.

## What a rule is

A rule defines three things:

1. A `Fields` Pydantic model (positive extraction fields: parties, dates, payment terms, amounts).
2. A `Clauses` Pydantic model (boolean flags for each clause type to look for, plus optional `*_evidence` strings).
3. The extraction prompt for Claude.

Each rule has a `rule_id` (stable identifier) and a `version` (semver). Files live at:

```
rules/<rule_id>/
├── __init__.py        # `from .vX_Y_Z import RULE` — pins the active version
├── v3_1_0.py          # current
├── v3_0_0.py          # previous (kept for record interpretability)
└── ...
```

Bumping the active version is editing the one-line `__init__.py`.

The folder map (`rules/folder_map.yaml`) maps directories under `WATCH_FOLDER`
to a `rule_id`. The ingestion service reads the map and the active rule
version at startup and stamps every extraction record with `(rule_id, rule_version)`.

## File shape

```python
# rules/saas_contract/v3_1_0.py
from datetime import date
from decimal import Decimal
from pydantic import Field
from shared.models import ContractFieldsBase, ClauseChecklistBase, Rule


class Fields(ContractFieldsBase):
    parties: list[str] = Field(description="...")
    effective_date: date | None = None
    expiry_date: date | None = None
    annual_value: Decimal | None = None
    currency: str | None = None
    # ...


class Clauses(ClauseChecklistBase):
    has_dr_clause: bool = Field(description="...")
    has_dr_clause_evidence: str | None = None
    # ...


PROMPT = "..."

RULE = Rule(
    rule_id="saas_contract",
    version="3.1.0",
    description="SaaS subscription contracts",
    extraction_prompt=PROMPT,
    fields_model=Fields,
    clauses_model=Clauses,
)
```

The Anthropic tool-use call uses `RULE.combined_tool_schema()` as the tool's
input schema; Claude returns `{fields, clauses, source_links}` in one call.

## Versioning policy

- **Patch (3.1.0 → 3.1.1).** Prompt clarification, no schema change. Existing records remain valid.
- **Minor (3.1.0 → 3.2.0).** Additive fields only (new `Optional` fields). Older records have nulls for new fields. No re-extraction required.
- **Major (3.1.0 → 4.0.0).** Breaking schema change (renamed/removed fields, type changes, removed clause checks). Re-extraction job required.

Old version files stay in the repo. Records extracted under the old version remain interpretable because the model class is still importable.

## Adding a new rule

1. Create `rules/<rule_id>/v1_0_0.py` with `Fields`, `Clauses`, `PROMPT`, `RULE`.
2. Create `rules/<rule_id>/__init__.py` with `from .v1_0_0 import RULE`.
3. Add `<rule_id>` to `KNOWN_RULES` in `rules/registry.py`.
4. If the rule applies to a folder, update `rules/folder_map.yaml`.
5. Run the extraction CLI against three sample documents and inspect the output. Don't release into the watcher until the samples look right.
6. Add a fixture PDF to `tests/fixtures/contracts/<rule_id>/` and a test that asserts extraction shape.

## Modifying an existing rule

Decide which version increment applies (patch, minor, major). Write the new file alongside the old one. Update `__init__.py` to import the new version. For a major version bump, also enqueue a re-extraction job for affected documents.

## What rules are not

- Not a query language. Rules define what gets extracted; queries operate on the result.
- Not user-editable in the POC. They're code, in git, reviewed.
- Not magic. If the source document doesn't contain the field, no rule recovers it.
- Not the right place to put business logic. "Flag contracts where renewal is within 90 days" is a query, not an extraction rule.

## Designing clause checks

The clause checklist is the commercially valuable part of the schema.

- **Phrase as presence questions, not values.** `has_dr_clause: bool` is a clean check. `dr_clause_text: str` invites hallucination and makes negative-space queries harder.
- **Be specific in the description.** "Has DR clause" is too vague for Claude to evaluate consistently. "Disaster recovery or business continuity obligations on the supplier, including RTO/RPO commitments or tested recovery procedures" gives a concrete test.
- **Pair the boolean with an evidence field if needed.** `has_dr_clause: bool` plus `has_dr_clause_evidence: str | None` with a quote and page reference is the pattern when the legal team wants to verify findings.
- **Keep the list focused.** Ten well-defined checks beat thirty fuzzy ones. Add checks as the audit needs surface, not speculatively.
