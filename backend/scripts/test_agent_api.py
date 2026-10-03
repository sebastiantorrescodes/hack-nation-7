"""Run one real API decision against a synthetic browser event."""

import asyncio
import sys

from pydantic import ValidationError

from app.agent.apprentice import process_event
from app.agent.schemas import CaptureContext, WorkMapState
from app.api_llm import ReasoningAPIError
from app.events.normalizer import normalize_browser_event


async def main() -> int:
    """Print only the validated decision; no API key or real screen data."""
    event = normalize_browser_event({
        "event_id": "demo-event-1", "event_type": "field_change",
        "target": "Review status", "old_value": "Pending", "new_value": "Needs review",
        "page": "Synthetic workflow demo",
    })
    try:
        decision = await process_event(event, WorkMapState(expert_name="Demo expert"),
                                       CaptureContext(expert_paused=True))
    except (ReasoningAPIError, ValidationError, ValueError) as error:
        # Validation errors can include model content, so keep those errors generic.
        message = str(error) if isinstance(error, ReasoningAPIError) else "Agent rejected the decision."
        print(message, file=sys.stderr)
        return 1
    print(decision.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
