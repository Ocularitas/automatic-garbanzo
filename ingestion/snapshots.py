"""Snapshot the corpus's extraction state to a JSON file, and diff two
snapshots field-by-field.

Used by the regression-diff workflow: snapshot → reextract → snapshot →
diff. Lets the operator see exactly what a rule or prompt change did to
existing extractions before merging it forward.

Snapshot shape (one entry per contract):

    {
      "captured_at": "2026-05-01T12:00:00Z",
      "contracts": [
        {
          "contract_id": "...",
          "document_id": "...",
          "rule_id": "saas_contract",
          "rule_version": "3.3.0",
          "file_path": "/.../foo.pdf",
          "promoted": {parties, effective_date, ...},
          "extracted": {...},
          "clauses": {...},
          "source_links": {...},
          "n_chunks": 4
        },
        ...
      ]
    }

Diff output is a structured changelog: per-contract entries listing
fields that changed, were added, were removed. Plus a per-rule summary.
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text

from shared.db import session_scope


# --- Snapshot ---------------------------------------------------------------

def snapshot_corpus(rule_id: str | None = None) -> dict[str, Any]:
    """Capture the current extracted state of every contract.

    `rule_id` optionally restricts to a single rule, useful for targeted
    regression diffs ("did the saas_contract 3.3.0 → 3.4.0 bump change
    anything outside SaaS?")."""
    sql = """
        SELECT c.id           AS contract_id,
               c.document_id  AS document_id,
               c.rule_id      AS rule_id,
               c.rule_version AS rule_version,
               c.parties, c.effective_date, c.expiry_date,
               c.currency, c.annual_value,
               c.extracted, c.clauses, c.source_links,
               d.file_path,
               (SELECT COUNT(*) FROM chunks WHERE document_id = c.document_id) AS n_chunks
          FROM contracts c JOIN documents d ON d.id = c.document_id
         {where}
         ORDER BY d.file_path
    """.format(where="WHERE c.rule_id = :rule_id" if rule_id else "")

    params: dict[str, Any] = {}
    if rule_id:
        params["rule_id"] = rule_id

    with session_scope() as s:
        rows = s.execute(text(sql), params).mappings().all()

    contracts: list[dict[str, Any]] = []
    for r in rows:
        contracts.append({
            "contract_id": str(r["contract_id"]),
            "document_id": str(r["document_id"]),
            "rule_id": r["rule_id"],
            "rule_version": r["rule_version"],
            "file_path": r["file_path"],
            "promoted": {
                "parties": r["parties"],
                "effective_date": r["effective_date"].isoformat() if r["effective_date"] else None,
                "expiry_date": r["expiry_date"].isoformat() if r["expiry_date"] else None,
                "currency": r["currency"],
                "annual_value": float(r["annual_value"]) if r["annual_value"] is not None else None,
            },
            "extracted": r["extracted"],
            "clauses": r["clauses"],
            "source_links": r["source_links"],
            "n_chunks": r["n_chunks"],
        })

    return {
        "captured_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds"),
        "rule_id_filter": rule_id,
        "contracts": contracts,
    }


def write_snapshot(snapshot: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, indent=2, default=str))


def read_snapshot(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


# --- Diff -------------------------------------------------------------------

@dataclass
class FieldChange:
    """One field that differs between two snapshots of the same contract."""
    field_path: str        # e.g. "clauses.has_dr_clause" or "promoted.expiry_date"
    before: Any
    after: Any
    kind: str              # "added" | "removed" | "changed" | "flipped"


@dataclass
class ContractDiff:
    contract_id: str
    document_id: str
    rule_id: str
    file_path: str
    rule_version_before: str | None = None
    rule_version_after: str | None = None
    n_chunks_before: int | None = None
    n_chunks_after: int | None = None
    changes: list[FieldChange] = field(default_factory=list)
    added: bool = False        # contract didn't exist before
    removed: bool = False      # contract existed before, gone now


@dataclass
class DiffSummary:
    captured_before: str
    captured_after: str
    contracts_total_before: int
    contracts_total_after: int
    contracts_added: int
    contracts_removed: int
    contracts_changed: int
    contracts_unchanged: int
    contract_diffs: list[ContractDiff] = field(default_factory=list)


# JSON paths we walk in the diff. Top-level keys map to the snapshot shape.
_DIFF_SECTIONS = ("promoted", "extracted", "clauses", "source_links")


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> DiffSummary:
    """Compare two snapshots and emit a structured diff per contract."""
    before_idx: dict[str, dict[str, Any]] = {
        c["contract_id"]: c for c in before.get("contracts", [])
    }
    after_idx: dict[str, dict[str, Any]] = {
        c["contract_id"]: c for c in after.get("contracts", [])
    }

    all_ids = set(before_idx) | set(after_idx)
    contract_diffs: list[ContractDiff] = []
    added = removed = changed = unchanged = 0

    for cid in sorted(all_ids):
        b = before_idx.get(cid)
        a = after_idx.get(cid)
        if b is None and a is not None:
            contract_diffs.append(ContractDiff(
                contract_id=cid,
                document_id=a["document_id"],
                rule_id=a["rule_id"],
                file_path=a["file_path"],
                rule_version_after=a["rule_version"],
                n_chunks_after=a.get("n_chunks"),
                added=True,
            ))
            added += 1
            continue
        if a is None and b is not None:
            contract_diffs.append(ContractDiff(
                contract_id=cid,
                document_id=b["document_id"],
                rule_id=b["rule_id"],
                file_path=b["file_path"],
                rule_version_before=b["rule_version"],
                n_chunks_before=b.get("n_chunks"),
                removed=True,
            ))
            removed += 1
            continue

        # Both present — diff the field tree.
        assert a is not None and b is not None
        changes = _diff_contract_fields(b, a)
        if not changes and a["rule_version"] == b["rule_version"] and a["n_chunks"] == b["n_chunks"]:
            unchanged += 1
            continue
        contract_diffs.append(ContractDiff(
            contract_id=cid,
            document_id=a["document_id"],
            rule_id=a["rule_id"],
            file_path=a["file_path"],
            rule_version_before=b["rule_version"],
            rule_version_after=a["rule_version"],
            n_chunks_before=b.get("n_chunks"),
            n_chunks_after=a.get("n_chunks"),
            changes=changes,
        ))
        changed += 1

    return DiffSummary(
        captured_before=before.get("captured_at", "?"),
        captured_after=after.get("captured_at", "?"),
        contracts_total_before=len(before_idx),
        contracts_total_after=len(after_idx),
        contracts_added=added,
        contracts_removed=removed,
        contracts_changed=changed,
        contracts_unchanged=unchanged,
        contract_diffs=contract_diffs,
    )


def _diff_contract_fields(b: dict[str, Any], a: dict[str, Any]) -> list[FieldChange]:
    """Compare each section of two contract snapshots, returning a flat list
    of FieldChange entries with dotted paths (e.g. 'clauses.has_dr_clause')."""
    out: list[FieldChange] = []
    for section in _DIFF_SECTIONS:
        bsec = b.get(section) or {}
        asec = a.get(section) or {}
        out.extend(_diff_dict(section, bsec, asec))
    return out


def _diff_dict(prefix: str, b: dict[str, Any], a: dict[str, Any]) -> list[FieldChange]:
    out: list[FieldChange] = []
    keys = set(b) | set(a)
    for k in sorted(keys):
        path = f"{prefix}.{k}"
        bv = b.get(k)
        av = a.get(k)
        if k not in b:
            if av not in (None, "", [], {}):
                out.append(FieldChange(field_path=path, before=None, after=av, kind="added"))
        elif k not in a:
            if bv not in (None, "", [], {}):
                out.append(FieldChange(field_path=path, before=bv, after=None, kind="removed"))
        elif bv != av:
            kind = "flipped" if isinstance(bv, bool) and isinstance(av, bool) else "changed"
            out.append(FieldChange(field_path=path, before=bv, after=av, kind=kind))
    return out


# --- Markdown formatter -----------------------------------------------------

def format_diff_markdown(summary: DiffSummary) -> str:
    """Render the diff as a copy-pasteable markdown doc."""
    lines: list[str] = []
    lines.append("# Regression diff")
    lines.append("")
    lines.append(f"- **Before:** {summary.captured_before}")
    lines.append(f"- **After:** {summary.captured_after}")
    lines.append("")
    lines.append("| | Before | After |")
    lines.append("|---|---|---|")
    lines.append(f"| Contracts total | {summary.contracts_total_before} | {summary.contracts_total_after} |")
    lines.append(f"| Added | | {summary.contracts_added} |")
    lines.append(f"| Removed | {summary.contracts_removed} | |")
    lines.append(f"| Changed | | {summary.contracts_changed} |")
    lines.append(f"| Unchanged | | {summary.contracts_unchanged} |")
    lines.append("")

    if summary.contracts_changed == 0 and summary.contracts_added == 0 and summary.contracts_removed == 0:
        lines.append("_No changes._")
        return "\n".join(lines)

    for cd in summary.contract_diffs:
        name = Path(cd.file_path).name if cd.file_path else cd.contract_id
        if cd.added:
            lines.append(f"## ➕ {name} ({cd.rule_id} {cd.rule_version_after})")
            lines.append("")
            lines.append("New contract.")
            lines.append("")
            continue
        if cd.removed:
            lines.append(f"## ➖ {name} ({cd.rule_id} {cd.rule_version_before})")
            lines.append("")
            lines.append("Removed.")
            lines.append("")
            continue
        version_note = ""
        if cd.rule_version_before != cd.rule_version_after:
            version_note = f" (rule version {cd.rule_version_before} → {cd.rule_version_after})"
        chunks_note = ""
        if cd.n_chunks_before != cd.n_chunks_after:
            chunks_note = f" — chunks: {cd.n_chunks_before} → {cd.n_chunks_after}"
        lines.append(f"## ✏️ {name} ({cd.rule_id}){version_note}{chunks_note}")
        lines.append("")
        if not cd.changes:
            lines.append("_(only metadata changed)_")
            lines.append("")
            continue
        lines.append("| Field | Before | After | Kind |")
        lines.append("|---|---|---|---|")
        for ch in cd.changes:
            lines.append(
                f"| `{ch.field_path}` | {_md_cell(ch.before)} | {_md_cell(ch.after)} | {ch.kind} |"
            )
        lines.append("")
    return "\n".join(lines)


def _md_cell(value: Any) -> str:
    """Render a value compactly for a markdown table cell."""
    if value is None:
        return "_null_"
    s = json.dumps(value, default=str)
    if len(s) > 120:
        s = s[:117] + "..."
    return s.replace("|", "\\|")
