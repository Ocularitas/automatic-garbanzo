"""Validate that rule schemas accept realistic shapes and reject bad ones.

No network. Exercises the same Pydantic validation path the extractor uses
when it receives Claude's tool output.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from rules.registry import get_rule


def test_saas_contract_accepts_realistic_payload() -> None:
    rule = get_rule("saas_contract")
    fields = rule.fields_model.model_validate({
        "parties": ["Acme Ltd", "Globex Inc"],
        "effective_date": "2026-01-01",
        "expiry_date": "2029-01-01",
        "auto_renewal": True,
        "auto_renewal_notice_days": 90,
        "payment_terms_days": 30,
        "annual_value": "120000.00",
        "currency": "GBP",
        "governing_law": "England and Wales",
        "termination_for_convenience_notice_days": None,
        "data_return_period_days": 30,
        "data_breach_notification_window_hours": 72,
    })
    assert fields.parties == ["Acme Ltd", "Globex Inc"]
    assert fields.effective_date == date(2026, 1, 1)
    assert fields.annual_value == Decimal("120000.00")
    assert fields.data_breach_notification_window_hours == 72

    clauses = rule.clauses_model.model_validate({
        "has_dr_clause": True,
        "has_dr_clause_evidence": "The supplier shall maintain ...",
        "has_data_residency_clause": False,
        "has_indemnity_cap": True,
        "has_indemnity_cap_evidence": "Liability is capped at 150% of fees paid.",
        "has_audit_rights": True,
        "has_change_of_control_clause": False,
        "has_data_breach_notification": True,
        "has_data_breach_notification_evidence": "Notify within 72 hours.",
        "has_data_breach_supercap": True,
        "has_data_breach_supercap_evidence": "Data breach liability is uncapped.",
        "has_ip_indemnity_carveout": False,
        "has_confidentiality_carveout": False,
        "has_wilful_default_carveout": True,
        # Phase 2 additions:
        "has_dpa_reference": True,
        "has_dpa_reference_evidence": "DPA at https://supplier.example/dpa applies.",
        "has_international_transfer_mechanism": True,
        "has_international_transfer_mechanism_evidence": "Transfers under UK IDTA.",
        "has_sub_processor_controls": True,
        "has_sub_processor_controls_evidence": "Customer prior written consent required.",
        "has_security_certifications": True,
        "has_security_certifications_evidence": "ISO 27001, SOC 2 Type II.",
        "has_data_return_clause": True,
        "has_data_return_clause_evidence": "Supplier shall return data within 30 days.",
    })
    assert clauses.has_dr_clause is True
    assert clauses.has_data_breach_supercap is True
    assert clauses.has_dpa_reference is True
    assert clauses.has_data_return_clause is True


def test_saas_contract_rejects_unknown_fields() -> None:
    rule = get_rule("saas_contract")
    with pytest.raises(ValidationError):
        rule.fields_model.model_validate({
            "parties": ["Acme"],
            "this_field_does_not_exist": "boom",
        })


def test_lease_accepts_break_dates_list() -> None:
    rule = get_rule("lease")
    fields = rule.fields_model.model_validate({
        "parties": ["LandlordCo", "TenantCo"],
        "property_address": "1 Example St, London EC1A 1AA",
        "effective_date": "2024-06-01",
        "expiry_date": "2034-05-31",
        "break_dates": ["2029-06-01"],
        "annual_value": "50000",
        "currency": "GBP",
        "rent_review_frequency_years": 5,
    })
    assert fields.break_dates == [date(2029, 6, 1)]
