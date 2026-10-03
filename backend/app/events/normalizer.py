# This file normalizes browser messages and vision output without calling models.

import re

from .schemas import ObservedEvent
from ..vision.schemas import ScreenObservation


_BROWSER_TYPES = {"click", "field_change", "navigation", "submit_attempt"}


def _legacy_browser_event(payload: dict) -> dict:
    """Adapt the current extension's messages without guessing missing facts."""
    allowed = {"type", "text", "frame", "t"}
    if set(payload) - allowed:
        raise ValueError("Unexpected fields in legacy browser event")
    kind = payload.get("type")
    text = payload.get("text", "")
    if not isinstance(text, str):
        raise ValueError("Browser action text must be a string")
    result = {
        "page": payload.get("frame", ""),
        "timestamp_ms": payload.get("t"),
        "description": text,
    }
    if kind == "save-attempt":
        return {**result, "event_type": "submit_attempt"}
    if kind != "ui-action":
        raise ValueError("Unsupported legacy browser event type")
    changed = re.fullmatch(r'changed "([^"\n]+)" to "([^"\n]*)"', text)
    if changed:
        return {
            **result, "event_type": "field_change",
            "target": changed[1], "new_value": changed[2],
        }
    clicked = re.fullmatch(r'clicked "([^"\n]+)"', text)
    if clicked:
        return {**result, "event_type": "click", "target": clicked[1]}
    raise ValueError("Unsupported or ambiguous browser action text; send a structured event")


def normalize_browser_event(payload: dict) -> ObservedEvent:
    """Normalize a structured browser event or the extension's legacy message."""
    if not isinstance(payload, dict):
        raise ValueError("Browser event must be a dictionary")
    event = dict(payload) if "event_type" in payload else _legacy_browser_event(payload)
    kind = event.get("event_type")
    if not isinstance(kind, str) or kind not in _BROWSER_TYPES:
        raise ValueError("Unsupported browser event type")
    if "source" in event and event["source"] != "browser":
        raise ValueError("Browser events must have browser provenance")
    event["source"] = "browser"
    event.setdefault("confidence", 1.0)
    return ObservedEvent.model_validate(event)


def normalize_vision_observation(
    observation: ScreenObservation,
    *,
    page: str | None = None,
    observation_id: str | None = None,
    timestamp_ms: int | None = None,
) -> list[ObservedEvent]:
    """Convert visible elements to observations, never inferred clicks or changes."""
    common = {
        "source": "vision",
        "source_event_id": observation_id,
        "timestamp_ms": timestamp_ms,
        "event_type": "screen_observation",
        "page": observation.page_description if page is None else page,
        "confidence": observation.confidence,
    }
    events = []
    for element in observation.visible_elements:
        box = element.bounding_box
        events.append(ObservedEvent(
            **common,
            target=element.label if element.label is not None else element.description,
            new_value=element.value,
            description=element.description,
            bounding_box=box,
            x_norm=(box.x_min + box.x_max) / 2 if box else None,
            y_norm=(box.y_min + box.y_max) / 2 if box else None,
        ))
    if observation.possible_event is not None:
        # This is model-described visible status, not a verified browser action.
        events.append(ObservedEvent(**common, description=observation.possible_event))
    if not events:
        events.append(ObservedEvent(**common, description=observation.page_description))
    return events
