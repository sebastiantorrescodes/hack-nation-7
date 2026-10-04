"""Turn a snapshot of the app's page (text the extension scraped from every frame) into a record,
using the workflow's own fields."""

from .llm import fields_doc, structured
from .models import RecordField, Workflow

_TYPES = {
    "list": {"type": ["array", "null"], "items": {"type": "string"}},
    "number": {"type": ["number", "null"]},
    "boolean": {"type": ["boolean", "null"]},
    "string": {"type": ["string", "null"]},
}


def _schema(fields: list[RecordField]) -> dict:
    return {
        "type": "object",
        "properties": {f.name: _TYPES[f.type] for f in fields},
        "required": [f.name for f in fields],
        "additionalProperties": False,
    }


class NoFields(Exception):
    """The workflow doesn't know what its records look like yet."""


async def extract_record(wf: Workflow, page: str, image_base64: str | None = None) -> dict:
    if not wf.fields:
        raise NoFields(f'"{wf.name}" has no record fields yet. Record a session or add fields first.')
    system = (
        f"You read a text snapshot of a screen in {wf.app or 'a web app'} and fill in the record it shows, "
        f"for the workflow \"{wf.name}\". Use null for anything not visible, including lists. An empty list means you observed that the list is empty. Never guess or turn unknown booleans into false. "
        "Copy codes and identifiers exactly as shown, without their descriptions.\n\nFields:\n" + fields_doc(wf.fields)
    )
    content: list[dict] = [{"type": "text", "text": page[:60000]}]
    if image_base64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_base64}})
    return await structured(effort="low", max_tokens=4000, system=system, content=content, schema=_schema(wf.fields))
