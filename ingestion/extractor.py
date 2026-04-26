"""Claude-based structured extraction via tool use."""
from __future__ import annotations

import base64
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import anthropic
from pydantic import ValidationError

from shared.config import get_settings
from shared.models import ClauseChecklistBase, ContractFieldsBase, Rule

TOOL_NAME = "record_extracted_fields"


@dataclass
class ExtractionResult:
    fields: ContractFieldsBase
    clauses: ClauseChecklistBase
    source_links: dict[str, Any]
    raw_response: dict[str, Any]


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_settings().anthropic_api_key)


def extract_contract(rule: Rule, pdf_path: Path) -> ExtractionResult:
    pdf_b64 = base64.standard_b64encode(pdf_path.read_bytes()).decode("ascii")
    return _extract(rule, pdf_b64, retry_payload=None)


def _extract(
    rule: Rule,
    pdf_b64: str,
    retry_payload: dict[str, Any] | None,
) -> ExtractionResult:
    settings = get_settings()
    tool_def = {
        "name": TOOL_NAME,
        "description": (
            f"Record extracted fields from a {rule.rule_id} document. "
            "Call this tool exactly once with the structured result."
        ),
        "input_schema": rule.combined_tool_schema(),
    }

    user_content: list[dict[str, Any]] = [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": pdf_b64,
            },
        },
        {"type": "text", "text": rule.extraction_prompt},
    ]
    if retry_payload is not None:
        user_content.append({
            "type": "text",
            "text": (
                "Your previous tool call did not validate against the schema:\n"
                f"{retry_payload['error']}\n"
                "Re-issue the tool call with the corrections."
            ),
        })

    response = _client().messages.create(
        model=settings.anthropic_model,
        max_tokens=8192,
        tools=[tool_def],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": user_content}],
    )

    payload = _extract_tool_payload(response)

    try:
        fields = rule.fields_model.model_validate(payload.get("fields", {}))
        clauses = rule.clauses_model.model_validate(payload.get("clauses", {}))
    except ValidationError as e:
        if retry_payload is not None:
            raise
        return _extract(rule, pdf_b64, retry_payload={"error": str(e)})

    return ExtractionResult(
        fields=fields,
        clauses=clauses,
        source_links=payload.get("source_links", {}) or {},
        raw_response=response.model_dump(mode="json"),
    )


def _extract_tool_payload(response: anthropic.types.Message) -> dict[str, Any]:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_NAME:
            return dict(block.input)
    raise RuntimeError(
        f"Model did not call the {TOOL_NAME!r} tool. "
        f"Stop reason: {response.stop_reason}."
    )
