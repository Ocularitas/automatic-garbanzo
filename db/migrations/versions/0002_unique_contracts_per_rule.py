"""Replace 3-column unique on contracts with (document_id, rule_id).

The 3-column form `(document_id, rule_id, rule_version)` allowed multiple
contract rows for the same document under different rule versions. That's
useful for an audit-trail tool but unhelpful for query: the MCP tools
return all rows, so a doc that's been extracted twice (e.g. after a rule
bump) appears twice in `list_contracts`.

For the POC we want "latest extraction per (document, rule)" semantics.
The raw_response JSONB on each row preserves the model output if anyone
needs to reconstruct prior versions later.

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_contracts_doc_rule_version", "contracts", type_="unique")
    op.create_unique_constraint(
        "uq_contracts_doc_rule", "contracts", ["document_id", "rule_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_contracts_doc_rule", "contracts", type_="unique")
    op.create_unique_constraint(
        "uq_contracts_doc_rule_version",
        "contracts",
        ["document_id", "rule_id", "rule_version"],
    )
