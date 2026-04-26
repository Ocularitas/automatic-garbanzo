"""Property leases."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from shared.models import ClauseChecklistBase, ContractFieldsBase, Rule


class Fields(ContractFieldsBase):
    parties: list[str] = Field(description="Landlord and tenant legal entity names.")
    property_address: str | None = Field(
        default=None, description="Full address of the leased property."
    )
    effective_date: date | None = Field(
        default=None, description="Lease commencement date."
    )
    expiry_date: date | None = Field(
        default=None, description="Lease end date (the contractual term end)."
    )
    break_dates: list[date] | None = Field(
        default=None, description="Tenant break option dates, if any."
    )
    annual_value: Decimal | None = Field(
        default=None, description="Annual rent as a number."
    )
    currency: str | None = Field(default=None, description="ISO 4217 currency code.")
    rent_review_frequency_years: int | None = Field(
        default=None, description="Years between rent reviews. Null if none."
    )
    deposit_amount: Decimal | None = None
    governing_law: str | None = None


class Clauses(ClauseChecklistBase):
    has_break_clause: bool = Field(
        description="Tenant has a contractual right to terminate before expiry."
    )
    has_subletting_restriction: bool = Field(
        description="Restriction or consent requirement on subletting / assignment."
    )
    has_repairing_obligations: bool = Field(
        description='Tenant repairing obligations defined (e.g. "full repairing and insuring").'
    )
    has_service_charge: bool = Field(description="Service charge or estate charge payable by tenant.")
    has_rent_review: bool = Field(description="Periodic rent review mechanism.")


PROMPT = """\
You are extracting structured fields from a property lease.

Field rules:
- Dates ISO 8601 (YYYY-MM-DD). Null if absent.
- Monetary amounts: number + ISO 4217 currency code.
- Do not guess. If a field is genuinely absent, return null.

Clause-flag rules (non-negotiable):
- For each `has_*` flag, set `true` only if the contract clearly contains the \
clause described. A vague reference does not count.
- If you set a flag to `true`, you MUST populate `source_links[<flag_name>]` \
with the page number and a verbatim quote.
- If you cannot find a verbatim quote that supports the flag, the flag is `false`.
- For every populated field, populate `source_links[<field_name>]` with page \
number and a verbatim quote.
"""


RULE = Rule(
    rule_id="lease",
    version="1.0.0",
    description="Property leases",
    extraction_prompt=PROMPT,
    fields_model=Fields,
    clauses_model=Clauses,
)
