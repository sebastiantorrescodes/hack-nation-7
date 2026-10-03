"""Claude calls. Every call returns JSON constrained by a schema (structured outputs)."""

import json

import anthropic

from .config import CLAUDE_FAST_MODEL, CLAUDE_MODEL
from .models import CLAIM_FIELDS

client = anthropic.AsyncAnthropic()


class LLMRefusal(Exception):
    pass


async def structured(
    *,
    system: str,
    content: list[dict] | str,
    schema: dict,
    model: str = CLAUDE_MODEL,
    effort: str = "medium",
    max_tokens: int = 16000,
) -> dict:
    # fallbacks="default": if a safety classifier declines, the API re-runs the
    # request on Anthropic's recommended fallback model instead of refusing.
    response = await client.beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason == "refusal":
        raise LLMRefusal(getattr(response.stop_details, "explanation", None) or "Claude declined this request")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude hit max_tokens before finishing the JSON output")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


# --- Schemas ----------------------------------------------------------------
def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": required if required is not None else list(props),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_NUM = {"type": "number"}

CONDITION_SCHEMA = _obj(
    {
        "field": {"type": "string", "enum": list(CLAIM_FIELDS)},
        "op": {
            "type": "string",
            "enum": ["eq", "neq", "in", "not_in", "contains", "not_contains", "gt", "lt", "is_true", "is_false", "empty", "not_empty"],
        },
        "values": {"type": "array", "items": _STR},
    }
)

FRAME_SCHEMA = _obj(
    {
        "screen_summary": _STR,
        "claim_fields_json": _STR,
        "changed": _BOOL,
        "event_kind": _STR,
        "event_description": _STR,
        "is_decision_point": _BOOL,
        "ask_why": _STR,
    }
)

WORKMAP_SCHEMA = _obj(
    {
        "summary": _STR,
        "skills": {
            "type": "array",
            "items": _obj(
                {
                    "title": _STR,
                    "trigger": {"type": "array", "items": CONDITION_SCHEMA},
                    "action": _obj(
                        {
                            "kind": {
                                "type": "string",
                                "enum": ["add_modifier", "remove_modifier", "change_code", "hold_claim", "query_physician", "submit", "other"],
                            },
                            "detail": _STR,
                        }
                    ),
                    "expert_explanation": _STR,
                    "guardrail": _obj({"description": _STR, "must": {"type": "array", "items": CONDITION_SCHEMA}}),
                    "evidence": {"type": "array", "items": _obj({"t": _NUM, "quote": _STR})},
                }
            ),
        },
    }
)

GRADE_SCHEMA = _obj({"correct": _BOOL, "feedback": _STR})

REPORT_SCHEMA = _obj(
    {
        "headline": _STR,
        "skills": {
            "type": "array",
            "items": _obj(
                {
                    "skill_id": _STR,
                    "status": {"type": "string", "enum": ["mastered", "practicing", "not_yet"]},
                    "note": _STR,
                }
            ),
        },
        "practice_next": {"type": "array", "items": _STR},
    }
)


def claim_fields_doc() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in CLAIM_FIELDS.items())


__all__ = [
    "structured",
    "LLMRefusal",
    "CLAUDE_MODEL",
    "CLAUDE_FAST_MODEL",
    "FRAME_SCHEMA",
    "WORKMAP_SCHEMA",
    "GRADE_SCHEMA",
    "REPORT_SCHEMA",
    "claim_fields_doc",
]
