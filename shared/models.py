"""Shared Pydantic models.

Two layers:
- Rule definition primitives (`Rule`, base classes for fields/clauses).
- DB row models (mirror the Postgres schema, used by ingestion writes and MCP reads).

Each rule lives in `rules/<rule_id>/v<version>.py` as a Python module that defines
a fields model, a clauses model, and a `RULE = Rule(...)` registration.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# --- Rule definition primitives -----------------------------------------------

class ContractFieldsBase(BaseModel):
    """Base class for the positive-extraction fields of a rule.

    Subclasses define typed fields (parties, dates, amounts, etc.). The subclass
    is fed into the Anthropic tool-use call as the tool's input schema.
    """
    model_config = ConfigDict(extra="forbid")


class ClauseChecklistBase(BaseModel):
    """Base class for clause-presence flags.

    Subclasses declare `has_*: bool` fields plus optional `*_evidence: str | None`
    fields. Stored in the contracts.clauses JSONB column.
    """
    model_config = ConfigDict(extra="forbid")


class FieldSourceLink(BaseModel):
    """Where in the source document an extracted field came from."""
    page: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    quote: str | None = None


class Rule(BaseModel):
    """Registration object for a versioned extraction rule."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    rule_id: str
    version: str
    description: str
    extraction_prompt: str
    fields_model: type[ContractFieldsBase]
    clauses_model: type[ClauseChecklistBase]

    # Optional list of common scalar fields the rule promises to populate, used
    # to project values into the promoted columns on `contracts`. Names must
    # exist on `fields_model`. Anything not listed stays in the JSONB blob.
    promoted_fields: ClassVar[tuple[str, ...]] = (
        "parties", "effective_date", "expiry_date", "currency", "annual_value",
    )

    def combined_tool_schema(self) -> dict[str, Any]:
        """Build the JSON schema for the extraction tool call.

        Composes fields, clauses, and source_links into one object the model
        fills in a single tool invocation.
        """
        fields_schema = self.fields_model.model_json_schema()
        clauses_schema = self.clauses_model.model_json_schema()
        return {
            "type": "object",
            "properties": {
                "fields": fields_schema,
                "clauses": clauses_schema,
                "source_links": {
                    "type": "object",
                    "description": (
                        "Map of field name to {page, char_start, char_end, quote}. "
                        "Provide for every field you populate."
                    ),
                    "additionalProperties": FieldSourceLink.model_json_schema(),
                },
            },
            "required": ["fields", "clauses"],
            "additionalProperties": False,
        }


# --- DB row models ------------------------------------------------------------

class DocumentRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    content_hash: str
    file_path: str
    mime_type: str
    byte_size: int
    rule_id: str
    rule_version: str
    user_id: str
    group_id: str
    created_at: datetime
    updated_at: datetime


class JobRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID | None
    content_hash: str
    file_path: str
    rule_id: str | None
    rule_version: str | None
    status: str
    attempt_count: int
    error_message: str | None
    user_id: str
    group_id: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ContractRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    rule_id: str
    rule_version: str
    parties: list[str] | None
    effective_date: date | None
    expiry_date: date | None
    currency: str | None
    annual_value: Decimal | None
    extracted: dict[str, Any]
    clauses: dict[str, Any]
    source_links: dict[str, Any]
    raw_response: dict[str, Any] | None
    user_id: str
    group_id: str
    created_at: datetime


class ChunkRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    chunk_index: int
    text: str
    page_start: int | None
    page_end: int | None
    char_start: int | None
    char_end: int | None
    rule_id: str
    rule_version: str
    user_id: str
    group_id: str
    created_at: datetime


# --- API result shapes (used by MCP tools) ------------------------------------

class ChunkSearchHit(BaseModel):
    document_id: UUID
    chunk_id: UUID
    chunk_index: int
    text: str
    page_start: int | None
    page_end: int | None
    score: float = Field(description="Cosine similarity in [0, 1].")
    rule_id: str
    file_path: str


class ContractSummary(BaseModel):
    """Lightweight contract row for list/aggregation tools."""
    contract_id: UUID
    document_id: UUID
    file_path: str
    rule_id: str
    rule_version: str
    parties: list[str] | None
    effective_date: date | None
    expiry_date: date | None
    currency: str | None
    annual_value: Decimal | None


class ClauseGap(BaseModel):
    contract_id: UUID
    document_id: UUID
    file_path: str
    rule_id: str
    parties: list[str] | None
    expiry_date: date | None
