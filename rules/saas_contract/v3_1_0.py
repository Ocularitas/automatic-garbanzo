"""SaaS subscription contracts."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import Field

from shared.models import ClauseChecklistBase, ContractFieldsBase, Rule


class Fields(ContractFieldsBase):
    parties: list[str] = Field(
        description="Legal entity names of the contracting parties."
    )
    effective_date: date | None = Field(
        default=None, description="Contract effective date (ISO 8601)."
    )
    expiry_date: date | None = Field(
        default=None,
        description="Contract expiry / end date. Null if perpetual or evergreen.",
    )
    auto_renewal: bool | None = Field(
        default=None, description="True if the contract auto-renews unless cancelled."
    )
    auto_renewal_notice_days: int | None = Field(
        default=None,
        description="Days of notice required to prevent auto-renewal.",
    )
    payment_terms_days: int | None = Field(
        default=None, description='Net payment days, e.g. 30 for "net 30".'
    )
    annual_value: Decimal | None = Field(
        default=None, description="Annual contract value as a number."
    )
    currency: str | None = Field(
        default=None, description="ISO 4217 currency code, e.g. GBP, USD, EUR."
    )
    governing_law: str | None = Field(
        default=None,
        description='Governing law jurisdiction, e.g. "England and Wales".',
    )
    termination_for_convenience_notice_days: int | None = Field(
        default=None,
        description="Notice days for termination for convenience. Null if not permitted.",
    )


class Clauses(ClauseChecklistBase):
    has_dr_clause: bool = Field(
        description=(
            "Disaster recovery or business continuity obligations on the supplier, "
            "including RTO/RPO commitments or tested recovery procedures."
        )
    )
    has_dr_clause_evidence: str | None = Field(
        default=None, description="Verbatim quote, if present."
    )
    has_data_residency_clause: bool = Field(
        description="Restrictions on where customer data may be stored or processed."
    )
    has_indemnity_cap: bool = Field(
        description="Cap on supplier liability for IP or third-party claims."
    )
    has_audit_rights: bool = Field(
        description="Customer right to audit supplier compliance."
    )
    has_change_of_control_clause: bool = Field(
        description=(
            "Provisions triggered by acquisition or material ownership change, "
            "e.g. termination right or notice obligation."
        )
    )
    has_data_breach_notification: bool = Field(
        description="Supplier obligation to notify customer of data breaches within a set period."
    )


PROMPT = """\
You are extracting structured fields from a SaaS subscription contract.

Rules:
- Be strict on dates: ISO 8601 only (YYYY-MM-DD). If a date is genuinely absent, return null.
- Capture monetary amounts as a number plus a separate ISO 4217 currency code.
- Do not guess. If a field is not stated, return null.
- For each clause check, return a boolean. Use the description as the test \
of presence; a vague reference does not count.
- For every populated field, also populate `source_links[<field_name>]` with \
the page number and a verbatim quote from the document.
"""


RULE = Rule(
    rule_id="saas_contract",
    version="3.1.0",
    description="SaaS subscription contracts",
    extraction_prompt=PROMPT,
    fields_model=Fields,
    clauses_model=Clauses,
)
