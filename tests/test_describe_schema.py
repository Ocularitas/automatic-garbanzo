"""Unit tests for `_build_schema_payload` — the engine behind `describe_schema`.

The corpus stats path needs a DB; here we only exercise the rules-only path
(`include_corpus=False`), which is pure introspection over the registry.
"""
from __future__ import annotations

from mcp_servers.query.server import _build_schema_payload, _type_str


def test_type_str_renders_human_readable() -> None:
    from datetime import date
    from decimal import Decimal
    assert _type_str(int) == "<class 'int'>" or "int" in _type_str(int)
    assert _type_str(int | None) == "int | null"
    assert _type_str(list[str]) == "list[str]"
    assert _type_str(date | None) == "date | null"
    assert _type_str(Decimal | None) == "Decimal | null"


def test_schema_payload_lists_active_rules_with_fields_and_flags() -> None:
    payload = _build_schema_payload(include_corpus=False)
    assert "rules" in payload
    assert "corpus" not in payload  # we asked for include_corpus=False

    rule_ids = {r["rule_id"] for r in payload["rules"]}
    assert {"saas_contract", "services_contract", "lease", "generic_contract"} <= rule_ids

    saas = next(r for r in payload["rules"] if r["rule_id"] == "saas_contract")
    assert saas["version"].startswith("3.")            # currently 3.3.0
    assert saas["description"]
    assert saas["promoted_scalar_columns"] == [
        "parties", "effective_date", "expiry_date", "currency", "annual_value",
    ]

    field_names = {f["name"] for f in saas["fields"]}
    assert {"parties", "effective_date", "expiry_date", "currency",
            "annual_value", "data_breach_notification_window_hours"} <= field_names

    flag_names = {c["name"] for c in saas["clause_flags"]}
    # Spot-check a flag from each generation of the rule
    assert "has_dr_clause" in flag_names           # in 3.1.0
    assert "has_data_breach_supercap" in flag_names  # added in 3.2.0
    assert "has_dpa_reference" in flag_names       # added in 3.3.0

    # Evidence fields are NOT surfaced as separate flags (paired via evidence_field)
    assert "has_dr_clause_evidence" not in flag_names
    paired = next(c for c in saas["clause_flags"] if c["name"] == "has_dr_clause")
    assert paired["evidence_field"] == "has_dr_clause_evidence"
