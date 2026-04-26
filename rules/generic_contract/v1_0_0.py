"""Generic contract fallback. Minimal common fields, conservative clause set."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from shared.models import ClauseChecklistBase, ContractFieldsBase, Rule


class Fields(ContractFieldsBase):
    parties: list[str] = Field(description="Legal entity names of the contracting parties.")
    effective_date: date | None = None
    expiry_date: date | None = None
    annual_value: Decimal | None = None
    currency: str | None = Field(default=None, description="ISO 4217 currency code.")
    governing_law: str | None = None
    document_type_guess: str | None = Field(
        default=None,
        description=(
            "Best guess at the document type, e.g. 'NDA', 'MSA', 'order form', "
            "'employment contract'. Used to suggest a more specific rule."
        ),
    )


class Clauses(ClauseChecklistBase):
    has_confidentiality: bool = Field(description="Confidentiality / non-disclosure obligations.")
    has_indemnity_cap: bool = Field(description="Cap on indemnity or liability.")
    has_termination_for_convenience: bool = Field(
        description="Either party may terminate for convenience on notice."
    )
    has_governing_law: bool = Field(description="Governing law / jurisdiction stated.")


PROMPT = """\
You are extracting structured fields from a contract whose specific type is unknown.

Field rules:
- Dates ISO 8601 (YYYY-MM-DD). Null if absent.
- Monetary amounts: number + ISO 4217 currency code.
- Do not guess. If a field is genuinely absent, return null.
- `document_type_guess` is your best label for what kind of contract this is; \
it informs whether a more specific rule should be added later.

Clause-flag rules (non-negotiable):
- For each `has_*` flag, set `true` only if the contract clearly contains the \
clause described. A vague reference does not count.
- If you set a flag to `true`, you MUST populate `source_links[<flag_name>]` \
with the page number and a verbatim quote.
- If you cannot find a verbatim quote that supports the flag, the flag is `false`.
- For every populated field, populate `source_links[<field_name>]` with page \
number and verbatim quote.
"""


RULE = Rule(
    rule_id="generic_contract",
    version="1.0.0",
    description="Generic fallback for unclassified contracts",
    extraction_prompt=PROMPT,
    fields_model=Fields,
    clauses_model=Clauses,
)
