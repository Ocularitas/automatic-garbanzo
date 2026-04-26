"""Professional services / consulting contracts."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from shared.models import ClauseChecklistBase, ContractFieldsBase, Rule


class Fields(ContractFieldsBase):
    parties: list[str] = Field(description="Legal entity names of the contracting parties.")
    effective_date: date | None = None
    expiry_date: date | None = Field(
        default=None, description="End date of the engagement, if defined."
    )
    statement_of_work_summary: str | None = Field(
        default=None, description="One-sentence summary of the scope of services."
    )
    fee_model: str | None = Field(
        default=None,
        description='One of: "fixed_price", "time_and_materials", "milestone", "retainer".',
    )
    annual_value: Decimal | None = Field(
        default=None,
        description="Total or annualised contract value as a number.",
    )
    currency: str | None = Field(default=None, description="ISO 4217 currency code.")
    payment_terms_days: int | None = None
    governing_law: str | None = None
    termination_for_convenience_notice_days: int | None = None


class Clauses(ClauseChecklistBase):
    has_ip_assignment: bool = Field(
        description="Assignment of IP in deliverables to the customer."
    )
    has_subcontracting_restriction: bool = Field(
        description="Supplier requires consent before subcontracting work."
    )
    has_key_personnel_clause: bool = Field(
        description="Named key personnel commitments or replacement notification."
    )
    has_indemnity_cap: bool = Field(
        description="Cap on supplier liability for IP or third-party claims."
    )
    has_non_solicitation: bool = Field(
        description="Restriction on either party hiring the other's staff."
    )
    has_warranty_period: bool = Field(
        description="Defined warranty / defect-correction period after delivery."
    )


PROMPT = """\
You are extracting structured fields from a professional services / consulting contract.

Field rules:
- Dates ISO 8601 (YYYY-MM-DD). Null if absent.
- Monetary amounts: number + ISO 4217 currency code.
- `fee_model` must be one of the allowed enum values; if the contract does not \
clearly fit, return null.
- Do not guess. If a field is genuinely absent, return null.

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
    rule_id="services_contract",
    version="1.0.0",
    description="Professional services / consulting contracts",
    extraction_prompt=PROMPT,
    fields_model=Fields,
    clauses_model=Clauses,
)
