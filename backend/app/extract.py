"""Turn a snapshot of the OpenEMR page (text the extension scraped from every frame) into claim fields."""

from .llm import CLAUDE_FAST_MODEL, structured
from .models import CLAIM_FIELDS

_LIST_FIELDS = {"cpt_codes", "icd10_codes", "modifiers"}
_NUM_FIELDS = {"patient_age", "units", "charge_amount"}
_BOOL_FIELDS = {"same_day_procedure", "prior_auth_on_file", "documentation_complete"}


def _field_schema(name: str) -> dict:
    if name in _LIST_FIELDS:
        return {"type": "array", "items": {"type": "string"}}
    if name in _NUM_FIELDS:
        return {"type": ["number", "null"]}
    if name in _BOOL_FIELDS:
        return {"type": ["boolean", "null"]}
    return {"type": ["string", "null"]}


CLAIM_SCHEMA = {
    "type": "object",
    "properties": {k: _field_schema(k) for k in CLAIM_FIELDS},
    "required": list(CLAIM_FIELDS),
    "additionalProperties": False,
}

SYSTEM = (
    "You read a text snapshot of an OpenEMR screen (fee sheet, encounter, billing manager) and fill in the "
    "claim it shows. Use null (or [] for lists) for anything not visible - never guess. Codes go without "
    "descriptions (e.g. '99214', 'M17.11'). Modifiers attached to codes (e.g. '99214-25' or a modifier column) "
    "go in `modifiers`. Status: 'held' if the claim is on hold, 'submitted' if billed, else 'draft'.\n\nFields:\n"
    + "\n".join(f"- {k}: {v}" for k, v in CLAIM_FIELDS.items())
)


async def extract_claim(page: str, image_base64: str | None = None) -> dict:
    content: list[dict] = [{"type": "text", "text": page[:60000]}]
    if image_base64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_base64}})
    return await structured(model=CLAUDE_FAST_MODEL, effort="low", max_tokens=4000, system=SYSTEM, content=content, schema=CLAIM_SCHEMA)
