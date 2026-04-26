"""SaaS subscription contracts — v3.2.0.

Additive over v3.1.0:
- Carve-out flags for the indemnity cap (data breach, IP, confidentiality, wilful default).
- A matching `*_evidence` field for every clause flag, so any answer to
  "show me the evidence for clause X" is one structured query, not RAG fishing.
"""
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
    has_data_residency_clause_evidence: str | None = Field(
        default=None, description="Verbatim quote, if present."
    )

    has_indemnity_cap: bool = Field(
        description="Cap on supplier liability for IP or third-party claims."
    )
    has_indemnity_cap_evidence: str | None = Field(
        default=None, description="Verbatim quote of the cap clause, if present."
    )

    has_audit_rights: bool = Field(
        description="Customer right to audit supplier compliance."
    )
    has_audit_rights_evidence: str | None = Field(
        default=None, description="Verbatim quote, if present."
    )

    has_change_of_control_clause: bool = Field(
        description=(
            "Provisions triggered by acquisition or material ownership change, "
            "e.g. termination right or notice obligation."
        )
    )
    has_change_of_control_clause_evidence: str | None = Field(
        default=None, description="Verbatim quote, if present."
    )

    has_data_breach_notification: bool = Field(
        description="Supplier obligation to notify customer of data breaches within a set period."
    )
    has_data_breach_notification_evidence: str | None = Field(
        default=None, description="Verbatim quote, if present."
    )

    # Indemnity-cap carve-outs — what falls OUTSIDE the headline cap.
    has_data_breach_supercap: bool = Field(
        description=(
            "Data breach / personal-data liability is uncapped, super-capped "
            "(higher than the general cap), or otherwise carved out of the headline cap."
        )
    )
    has_data_breach_supercap_evidence: str | None = Field(default=None)

    has_ip_indemnity_carveout: bool = Field(
        description=(
            "IP infringement indemnity is uncapped, super-capped, or otherwise "
            "carved out of the headline liability cap."
        )
    )
    has_ip_indemnity_carveout_evidence: str | None = Field(default=None)

    has_confidentiality_carveout: bool = Field(
        description=(
            "Breach of confidentiality is uncapped or super-capped versus the "
            "general liability cap."
        )
    )
    has_confidentiality_carveout_evidence: str | None = Field(default=None)

    has_wilful_default_carveout: bool = Field(
        description=(
            "Wilful default, fraud, or gross negligence is uncapped versus the "
            "general liability cap. (Many UK contracts treat these as un-excludable "
            "by statute, but the explicit clause is what counts here.)"
        )
    )
    has_wilful_default_carveout_evidence: str | None = Field(default=None)


PROMPT = """\
You are extracting structured fields from a SaaS subscription contract.

Field rules:
- Dates ISO 8601 (YYYY-MM-DD) only. Null if absent.
- Monetary amounts: number + ISO 4217 currency code, separately.
- Do not guess. If a field is not clearly stated, return null.

Clause-flag rules (these are non-negotiable):
- For each `has_*` flag, set `true` only if the contract clearly contains the \
clause described. A vague reference does not count.
- If you set a `has_*` flag to `true`, you MUST also populate:
  (a) the matching `has_*_evidence` field with a verbatim quote from the document, AND
  (b) `source_links[<flag_name>]` with the page number and the same verbatim quote.
- If you cannot find a verbatim quote that supports the flag, the flag is `false`. \
No exceptions. A `true` flag without evidence is wrong and will be rejected.

Carve-out flags specifically:
- `has_data_breach_supercap`, `has_ip_indemnity_carveout`, \
`has_confidentiality_carveout`, `has_wilful_default_carveout`: these describe \
liability that is *outside* (uncapped, super-capped, or carved out of) the \
general liability cap. The headline cap clause itself is captured separately by \
`has_indemnity_cap`. Quote the exact language that establishes the carve-out, \
not the headline cap.
"""


RULE = Rule(
    rule_id="saas_contract",
    version="3.2.0",
    description="SaaS subscription contracts (with indemnity carve-outs)",
    extraction_prompt=PROMPT,
    fields_model=Fields,
    clauses_model=Clauses,
)
