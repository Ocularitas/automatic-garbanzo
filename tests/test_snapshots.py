"""Unit tests for the regression-diff snapshot/diff logic.

The DB-touching part (`snapshot_corpus`) isn't covered here — that's
exercised on the VM where the DB is real. We test the pure-Python diff
shape, which is the part that has to be right for the team to trust the
output."""
from __future__ import annotations

from ingestion.snapshots import (
    diff_snapshots,
    format_diff_markdown,
)


def _contract(cid: str, **overrides) -> dict:
    base = {
        "contract_id": cid,
        "document_id": f"d-{cid}",
        "rule_id": "saas_contract",
        "rule_version": "3.3.0",
        "file_path": f"/data/saas/{cid}.pdf",
        "promoted": {
            "parties": ["Customer", "Supplier"],
            "effective_date": "2026-01-01",
            "expiry_date": "2027-12-31",
            "currency": "GBP",
            "annual_value": 100000.0,
        },
        "extracted": {"annual_value": "100000", "payment_terms_days": 30},
        "clauses": {"has_dr_clause": True, "has_dpa_reference": False},
        "source_links": {"has_dr_clause": {"page": 4, "quote": "..."}},
        "n_chunks": 4,
    }
    base.update(overrides)
    return base


def _snapshot(*contracts, captured_at: str = "2026-05-01T12:00:00Z") -> dict:
    return {"captured_at": captured_at, "rule_id_filter": None,
            "contracts": list(contracts)}


def test_unchanged_corpus_produces_no_changes() -> None:
    before = _snapshot(_contract("c1"), _contract("c2"))
    after = _snapshot(_contract("c1"), _contract("c2"))
    s = diff_snapshots(before, after)
    assert s.contracts_unchanged == 2
    assert s.contracts_changed == 0
    assert s.contracts_added == 0
    assert s.contracts_removed == 0


def test_added_contract_detected() -> None:
    before = _snapshot(_contract("c1"))
    after = _snapshot(_contract("c1"), _contract("c2"))
    s = diff_snapshots(before, after)
    assert s.contracts_added == 1
    diff = next(d for d in s.contract_diffs if d.contract_id == "c2")
    assert diff.added is True


def test_removed_contract_detected() -> None:
    before = _snapshot(_contract("c1"), _contract("c2"))
    after = _snapshot(_contract("c1"))
    s = diff_snapshots(before, after)
    assert s.contracts_removed == 1
    diff = next(d for d in s.contract_diffs if d.contract_id == "c2")
    assert diff.removed is True


def test_promoted_field_change_detected() -> None:
    before = _snapshot(_contract("c1"))
    after_contract = _contract("c1")
    after_contract["promoted"] = {**after_contract["promoted"], "currency": "USD"}
    after = _snapshot(after_contract)
    s = diff_snapshots(before, after)
    assert s.contracts_changed == 1
    diff = s.contract_diffs[0]
    paths = {ch.field_path for ch in diff.changes}
    assert "promoted.currency" in paths
    change = next(c for c in diff.changes if c.field_path == "promoted.currency")
    assert change.before == "GBP"
    assert change.after == "USD"
    assert change.kind == "changed"


def test_clause_flag_flip_detected() -> None:
    before = _snapshot(_contract("c1"))
    after_contract = _contract("c1")
    after_contract["clauses"] = {**after_contract["clauses"], "has_dr_clause": False}
    after = _snapshot(after_contract)
    s = diff_snapshots(before, after)
    diff = s.contract_diffs[0]
    flip = next(c for c in diff.changes if c.field_path == "clauses.has_dr_clause")
    assert flip.kind == "flipped"
    assert flip.before is True
    assert flip.after is False


def test_newly_populated_extracted_field_detected() -> None:
    before = _snapshot(_contract("c1"))
    after_contract = _contract("c1")
    after_contract["extracted"] = {
        **after_contract["extracted"],
        "data_breach_notification_window_hours": 72,
    }
    after = _snapshot(after_contract)
    s = diff_snapshots(before, after)
    diff = s.contract_diffs[0]
    add = next(c for c in diff.changes
               if c.field_path == "extracted.data_breach_notification_window_hours")
    assert add.kind == "added"
    assert add.after == 72


def test_rule_version_bump_alone_is_a_change() -> None:
    """If a contract is re-extracted under a new rule version with identical
    field outputs, the rule_version change itself should still mark the
    contract as 'changed' (not 'unchanged')."""
    before = _snapshot(_contract("c1"))
    after = _snapshot(_contract("c1", rule_version="3.4.0"))
    s = diff_snapshots(before, after)
    assert s.contracts_changed == 1
    assert s.contracts_unchanged == 0


def test_chunk_count_change_alone_is_a_change() -> None:
    """Chunker tuning bumps n_chunks per document. Treat as a change so the
    operator can see the impact."""
    before = _snapshot(_contract("c1", n_chunks=4))
    after = _snapshot(_contract("c1", n_chunks=12))
    s = diff_snapshots(before, after)
    assert s.contracts_changed == 1


def test_format_diff_markdown_handles_empty_diff() -> None:
    s = diff_snapshots(_snapshot(_contract("c1")), _snapshot(_contract("c1")))
    md = format_diff_markdown(s)
    assert "Regression diff" in md
    assert "No changes" in md


def test_format_diff_markdown_renders_changes() -> None:
    before = _snapshot(_contract("c1"))
    after_contract = _contract("c1")
    after_contract["clauses"] = {**after_contract["clauses"], "has_dr_clause": False}
    after = _snapshot(after_contract)
    md = format_diff_markdown(diff_snapshots(before, after))
    assert "clauses.has_dr_clause" in md
    assert "flipped" in md
