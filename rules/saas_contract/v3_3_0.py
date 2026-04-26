"""SaaS subscription contracts — v3.3.0.

Additive over v3.2.0. Phase 2 of the extraction enhancement spec, adapted
to the existing boolean-with-evidence pattern (no enums, no nested objects):

  - Five new clause flags around data protection:
      has_dpa_reference, has_international_transfer_mechanism,
      has_sub_processor_controls, has_security_certifications,
      has_data_return_clause
  - Two scalar fields where structure is high-value for queries:
      data_return_period_days,
      data_breach_notification_window_hours

Enums for transfer mechanism, sub-processor control level, and the
certifications list are deferred — capturing the verbatim quote in the
matching `*_evidence` field gives analysts the structure-by-text without
locking in a taxonomy at N=6.
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

    # New scalars in 3.3.0:
    data_return_period_days: int | None = Field(
        default=None,
        description=(
            "If the contract requires the supplier to return or delete customer "
            "data on termination within a stated period, the period in days. "
            "Null if no period is stated even when a return obligation exists."
        ),
    )
    data_breach_notification_window_hours: int | None = Field(
        default=None,
        description=(
            "If the contract requires the supplier to notify the customer of a "
            "personal-data breach within a stated time, the window in hours "
            "(e.g. 72 for 'within 72 hours of awareness'). Null if no window is "
            "stated even when a notification obligation exists."
        ),
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
        description=(
            "Restrictions on where customer data may be physically stored or "
            "processed (e.g. 'data shall be hosted only in the UK and EEA'). "
            "This is a *location* restriction, not a legal-mechanism statement."
        )
    )
    has_data_residency_clause_evidence: str | None = Field(default=None)

    has_indemnity_cap: bool = Field(
        description="Cap on supplier liability for IP or third-party claims."
    )
    has_indemnity_cap_evidence: str | None = Field(default=None)

    has_audit_rights: bool = Field(
        description="Customer right to audit supplier compliance."
    )
    has_audit_rights_evidence: str | None = Field(default=None)

    has_change_of_control_clause: bool = Field(
        description=(
            "Provisions triggered by acquisition or material ownership change, "
            "e.g. termination right or notice obligation."
        )
    )
    has_change_of_control_clause_evidence: str | None = Field(default=None)

    has_data_breach_notification: bool = Field(
        description=(
            "Supplier obligation to notify customer of personal-data breaches "
            "within a defined timeframe. Capture the timeframe in "
            "`data_breach_notification_window_hours` if stated."
        )
    )
    has_data_breach_notification_evidence: str | None = Field(default=None)

    # Indemnity-cap carve-outs (from 3.2.0).
    has_data_breach_supercap: bool = Field(
        description=(
            "Data breach / personal-data liability is uncapped, super-capped "
            "(higher than the general cap), or otherwise carved out of the "
            "headline cap."
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
            "general liability cap."
        )
    )
    has_wilful_default_carveout_evidence: str | None = Field(default=None)

    # Phase 2: data protection cluster (new in 3.3.0).
    has_dpa_reference: bool = Field(
        description=(
            "A Data Processing Agreement (DPA) is referenced or incorporated, "
            "either as a separate annex or by reference to a published URL."
        )
    )
    has_dpa_reference_evidence: str | None = Field(default=None)

    has_international_transfer_mechanism: bool = Field(
        description=(
            "The contract names a legal mechanism for cross-border personal-data "
            "transfers (e.g. EU Standard Contractual Clauses, UK IDTA, an "
            "adequacy decision). Distinct from `has_data_residency_clause`: this "
            "is the *legal basis* if data leaves the home jurisdiction; data "
            "residency is whether data is allowed to leave at all."
        )
    )
    has_international_transfer_mechanism_evidence: str | None = Field(default=None)

    has_sub_processor_controls: bool = Field(
        description=(
            "The customer has rights over the supplier's use of sub-processors: "
            "prior consent, notification with right to object, or notification only. "
            "Quote the strongest control language in evidence."
        )
    )
    has_sub_processor_controls_evidence: str | None = Field(default=None)

    has_security_certifications: bool = Field(
        description=(
            "The supplier asserts holding or maintaining specific security "
            "certifications (e.g. ISO 27001, SOC 2, Cyber Essentials Plus). "
            "Quote the verbatim list in evidence."
        )
    )
    has_security_certifications_evidence: str | None = Field(default=None)

    has_data_return_clause: bool = Field(
        description=(
            "Supplier obligation to return or delete customer data on contract "
            "termination. If a stated period applies, capture it in "
            "`data_return_period_days`."
        )
    )
    has_data_return_clause_evidence: str | None = Field(default=None)


PROMPT = """\
You are extracting structured fields from a SaaS subscription contract.

Field rules:
- Dates ISO 8601 (YYYY-MM-DD) only. Null if absent.
- Monetary amounts: number + ISO 4217 currency code, separately.
- Do not guess. If a field is not clearly stated, return null.
- For period scalars (`data_return_period_days`, \
`data_breach_notification_window_hours`): only populate if the contract \
states a specific number. "Promptly" or "as soon as reasonably practicable" \
is null.

Clause-flag rules (non-negotiable):
- For each `has_*` flag, set `true` only if the contract clearly contains the \
clause described. A vague reference does not count.
- If you set a `has_*` flag to `true`, you MUST also populate:
  (a) the matching `has_*_evidence` field with a verbatim quote, AND
  (b) `source_links[<flag_name>]` with the page number and the same quote.
- If you cannot find a verbatim quote that supports the flag, the flag is \
`false`. No exceptions.

Indemnity carve-outs:
- `has_data_breach_supercap`, `has_ip_indemnity_carveout`, \
`has_confidentiality_carveout`, `has_wilful_default_carveout` describe \
liability that is *outside* (uncapped, super-capped, or carved out of) the \
general liability cap. The headline cap clause itself is captured by \
`has_indemnity_cap`. Quote the carve-out language, not the headline cap.

Data protection — distinguish carefully between:
- `has_data_residency_clause`: a *location* restriction, e.g. "Customer data \
shall be processed only within the UK and EEA."
- `has_international_transfer_mechanism`: a *legal mechanism* for cross-border \
transfer, e.g. "Where data is transferred outside the UK, transfers shall be \
made under the UK IDTA." A contract may have one without the other.

`has_sub_processor_controls`: set true if the customer has any control over \
sub-processors (prior consent, notification with objection, notification \
only). The evidence quote should show the strongest control language present.

`has_security_certifications`: only true if the contract names specific \
certifications (ISO 27001, SOC 2 Type I/II, Cyber Essentials, PCI DSS, etc.). \
A general "industry-standard security" statement does not count.
"""


RULE = Rule(
    rule_id="saas_contract",
    version="3.3.0",
    description="SaaS subscription contracts (with data-protection cluster)",
    extraction_prompt=PROMPT,
    fields_model=Fields,
    clauses_model=Clauses,
)
