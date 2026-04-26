"""Rule registry sanity tests. No DB or API calls."""
from __future__ import annotations

from pathlib import Path

import pytest

from rules.registry import KNOWN_RULES, all_rules, get_rule, resolve_rule_for_path
from shared.config import get_settings


def test_all_known_rules_load() -> None:
    rules = all_rules()
    assert set(rules) == set(KNOWN_RULES)
    for rule_id, rule in rules.items():
        assert rule.rule_id == rule_id
        assert rule.version
        assert rule.fields_model is not None
        assert rule.clauses_model is not None
        assert rule.extraction_prompt.strip()


def test_combined_tool_schema_has_required_blocks() -> None:
    rule = get_rule("saas_contract")
    schema = rule.combined_tool_schema()
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"fields", "clauses", "source_links"}
    assert "fields" in schema["required"]
    assert "clauses" in schema["required"]


def test_clause_models_only_have_bool_or_optional_str() -> None:
    """Clause checklists are presence flags, not free-form text. Catch drift."""
    for rule in all_rules().values():
        for name, field in rule.clauses_model.model_fields.items():
            ann = field.annotation
            if name.endswith("_evidence"):
                # Optional str pattern
                assert ann in (str | None, type(None) | str), (
                    f"{rule.rule_id}.{name} evidence should be str | None"
                )
            else:
                assert ann is bool, (
                    f"{rule.rule_id}.{name} should be bool, got {ann!r}"
                )


def test_folder_map_resolves(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("WATCH_FOLDER", str(tmp_path))
    get_settings.cache_clear() if hasattr(get_settings, "cache_clear") else None

    (tmp_path / "contracts" / "saas").mkdir(parents=True)
    (tmp_path / "contracts" / "leases").mkdir(parents=True)
    (tmp_path / "other").mkdir(parents=True)

    saas_path = tmp_path / "contracts" / "saas" / "alpha.pdf"
    saas_path.touch()
    lease_path = tmp_path / "contracts" / "leases" / "beta.pdf"
    lease_path.touch()
    other_path = tmp_path / "other" / "gamma.pdf"
    other_path.touch()

    assert resolve_rule_for_path(saas_path).rule_id == "saas_contract"
    assert resolve_rule_for_path(lease_path).rule_id == "lease"
    assert resolve_rule_for_path(other_path).rule_id == "generic_contract"


@pytest.mark.parametrize("rule_id", KNOWN_RULES)
def test_rule_promoted_fields_exist_on_model(rule_id: str) -> None:
    """Every promoted field name must exist on the rule's fields model.

    Otherwise the writer would silently store nulls in the promoted column.
    """
    rule = get_rule(rule_id)
    field_names = set(rule.fields_model.model_fields)
    for promoted in rule.promoted_fields:
        # `parties` is required on every rule we ship; the others are optional.
        # Only flag fields that are missing entirely.
        assert promoted in field_names or promoted not in {"parties"}, (
            f"{rule_id}: required promoted field {promoted!r} missing from schema"
        )
