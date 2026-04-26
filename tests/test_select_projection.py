"""Unit tests for `_project_select` — the new dotted/bare projection in
`query_contracts_structured`.

Covers the three select forms (top-level, dotted, bare leaf) plus the
unknown-selector error path that replaced the silent-drop behaviour, and
the clause-flag URL injection layered on top.

No DB required.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mcp_servers.query.store import _project_select, _resolve_select_target


@pytest.fixture
def watch_root(tmp_path: Path, monkeypatch) -> Path:
    """Real on-disk watch folder + PUBLIC_BASE_URL so build_document_url
    produces real URLs the projection can attach to flag selectors."""
    monkeypatch.setenv("WATCH_FOLDER", str(tmp_path))
    monkeypatch.setenv(
        "PUBLIC_BASE_URL",
        "https://cipoc.uksouth.cloudapp.azure.com/TOKEN",
    )
    (tmp_path / "saas").mkdir()
    (tmp_path / "saas" / "foo.pdf").touch()
    return tmp_path


def _row(file_path: str = "/data/saas/foo.pdf") -> dict:
    """Single fake row mirroring what query_contracts_structured returns."""
    return {
        "contract_id": "c-1",
        "document_id": "d-1",
        "file_path": file_path,
        "rule_id": "saas_contract",
        "rule_version": "3.3.0",
        "parties": ["Customer Ltd", "Supplier Ltd"],
        "effective_date": "2026-01-01",
        "expiry_date": "2027-12-31",
        "currency": "GBP",
        "annual_value": 145000.0,           # promoted column: float
        "extracted": {
            "annual_value": "145000",        # JSONB: Decimal-as-string
            "data_breach_notification_window_hours": 72,
            "data_return_period_days": 30,
        },
        "clauses": {
            "has_dr_clause": True,
            "has_dr_clause_evidence": "The supplier shall maintain ...",
            "has_dpa_reference": False,
            "has_data_breach_notification": True,
            "has_data_breach_notification_evidence": "Helio shall notify ...",
        },
        "source_links": {
            "has_dr_clause": {"page": 4, "quote": "..."},
            "has_data_breach_notification": {"page": 2, "quote": "Helio ..."},
        },
    }


def test_top_level_select_projects_column() -> None:
    out = _project_select([_row()], ["expiry_date"])
    assert out[0]["expiry_date"] == "2027-12-31"
    assert out[0]["contract_id"] == "c-1"  # always included


def test_dotted_into_extracted() -> None:
    out = _project_select([_row()], ["extracted.data_breach_notification_window_hours"])
    assert out[0]["extracted.data_breach_notification_window_hours"] == 72


def test_dotted_into_clauses() -> None:
    out = _project_select([_row()], ["clauses.has_dr_clause"])
    assert out[0]["clauses.has_dr_clause"] is True


def test_dotted_into_source_links() -> None:
    out = _project_select([_row()], ["source_links.has_dr_clause"])
    assert out[0]["source_links.has_dr_clause"] == {"page": 4, "quote": "..."}


def test_bare_leaf_resolves_against_extracted() -> None:
    out = _project_select([_row()], ["data_breach_notification_window_hours"])
    assert out[0]["data_breach_notification_window_hours"] == 72


def test_bare_leaf_resolves_against_clauses() -> None:
    out = _project_select([_row()], ["has_dpa_reference"])
    assert out[0]["has_dpa_reference"] is False


def test_top_level_wins_over_extracted_for_overlapping_names() -> None:
    """`annual_value` exists both as a promoted column (typed float) and inside
    extracted (Decimal-as-string). Bare lookup must prefer top-level."""
    out = _project_select([_row()], ["annual_value"])
    assert out[0]["annual_value"] == 145000.0  # not the string


def test_unknown_top_level_raises_with_inlined_valid_keys() -> None:
    # Error message must enumerate the actual valid top-level keys, not
    # reference an internal identifier. Saves a follow-up call.
    with pytest.raises(ValueError) as exc:
        _project_select([_row()], ["wibble"])
    msg = str(exc.value)
    assert "Unknown select target 'wibble'" in msg
    for key in ("contract_id", "expiry_date", "annual_value", "extracted",
                "clauses", "source_links"):
        assert key in msg, f"error message should inline {key!r}; got: {msg}"


def test_unknown_dotted_container_raises() -> None:
    with pytest.raises(ValueError, match="Dotted paths must start"):
        _project_select([_row()], ["wibble.foo"])


def test_invalid_leaf_name_raises() -> None:
    """Sanity-check the leaf-name validator (no SQL injection-style chars)."""
    with pytest.raises(ValueError, match="Invalid leaf name"):
        _project_select([_row()], ["extracted.bad name with space"])


def test_multiple_selectors_combine() -> None:
    out = _project_select(
        [_row()],
        ["expiry_date", "extracted.data_breach_notification_window_hours",
         "clauses.has_dr_clause"],
    )
    row = out[0]
    assert row["expiry_date"] == "2027-12-31"
    assert row["extracted.data_breach_notification_window_hours"] == 72
    assert row["clauses.has_dr_clause"] is True
    # Always-keep set
    assert "contract_id" in row and "document_id" in row and "file_path" in row


def test_clause_flag_select_injects_source_url(watch_root: Path) -> None:
    pdf = watch_root / "saas" / "foo.pdf"
    row = _row(file_path=str(pdf))
    out = _project_select([row], ["has_data_breach_notification"])
    r = out[0]
    assert r["has_data_breach_notification"] is True
    # Page-anchored URL was synthesized from source_links.<flag>.page
    assert r["has_data_breach_notification_source_url"] == (
        "https://cipoc.uksouth.cloudapp.azure.com/TOKEN/"
        "docs/saas/foo.pdf#page=2"
    )


def test_clause_flag_evidence_select_also_injects_source_url(watch_root: Path) -> None:
    """Selecting the evidence partner of a flag should still produce the URL,
    keyed by the bare flag name (not the evidence one)."""
    pdf = watch_root / "saas" / "foo.pdf"
    row = _row(file_path=str(pdf))
    out = _project_select([row], ["has_data_breach_notification_evidence"])
    r = out[0]
    assert "has_data_breach_notification_evidence" in r
    assert r["has_data_breach_notification_source_url"].endswith("#page=2")


def test_dotted_clause_select_injects_source_url(watch_root: Path) -> None:
    pdf = watch_root / "saas" / "foo.pdf"
    row = _row(file_path=str(pdf))
    out = _project_select([row], ["clauses.has_dr_clause"])
    r = out[0]
    assert r["clauses.has_dr_clause"] is True
    assert r["has_dr_clause_source_url"].endswith("#page=4")


def test_clause_flag_url_skipped_when_source_link_missing(watch_root: Path) -> None:
    pdf = watch_root / "saas" / "foo.pdf"
    row = _row(file_path=str(pdf))
    # `has_dpa_reference` exists in clauses but not in source_links — no URL.
    out = _project_select([row], ["has_dpa_reference"])
    assert "has_dpa_reference_source_url" not in out[0]


def test_non_clause_select_does_not_get_source_url(watch_root: Path) -> None:
    pdf = watch_root / "saas" / "foo.pdf"
    row = _row(file_path=str(pdf))
    out = _project_select([row], ["expiry_date"])
    assert all(not k.endswith("_source_url") for k in out[0])


def test_clause_flag_and_evidence_together_dont_double_inject(watch_root: Path) -> None:
    """When the agent selects both a flag and its evidence partner, we should
    still emit exactly one `<flag>_source_url`."""
    pdf = watch_root / "saas" / "foo.pdf"
    row = _row(file_path=str(pdf))
    out = _project_select(
        [row],
        ["has_data_breach_notification", "has_data_breach_notification_evidence"],
    )
    keys = list(out[0])
    assert keys.count("has_data_breach_notification_source_url") == 1


def test_resolve_returns_none_for_present_but_null_field() -> None:
    """Distinguishable behaviour: a leaf that exists with value None returns
    (None, key) — that's a real null. An *unknown* leaf raises. The two
    states are now distinguishable, which was the point."""
    row = _row()
    row["extracted"]["annual_value"] = None
    value, key = _resolve_select_target("extracted.annual_value", row)
    assert value is None
    assert key == "extracted.annual_value"