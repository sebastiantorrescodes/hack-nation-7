"""Free-only OpenRouter reasoning for the staged apprentice agent."""

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


DEFAULT_MODEL = "qwen/qwen3.8-27b:free"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"


class ReasoningAPIError(RuntimeError):
    """Report configuration or provider failure without logging private content."""


async def structured(*, system: str, content: str, schema: dict,
                     max_tokens: int = 3000, effort: str = "low",
                     transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Request schema-shaped JSON; the orchestrator validates it before dispatch."""
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    model = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL).strip()
    if not key:
        raise ReasoningAPIError("Set OPENROUTER_API_KEY in backend/.env first.")
    if not model.endswith(":free"):
        raise ReasoningAPIError("OPENROUTER_MODEL must end with :free; paid models are disabled.")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": content}],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "agent_decision", "strict": True, "schema": schema}},
        "provider": {"require_parameters": True,
                     "max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0}},
    }
    # Keep the caller-compatible effort argument without assuming every model supports it.
    try:
        async with httpx.AsyncClient(transport=transport, timeout=60) as client:
            response = await client.post(ENDPOINT, json=payload,
                headers={"Authorization": f"Bearer {key}"})
    except httpx.RequestError:
        raise ReasoningAPIError("OpenRouter connection failed; no agent action was executed.") from None
    if response.status_code != 200:
        guidance = {401: "Check your OpenRouter API key.",
                    402: "Free capacity or account access is unavailable; do not add paid credits.",
                    429: "Free requests are rate limited; try again later."}
        raise ReasoningAPIError(f"OpenRouter HTTP {response.status_code}. " +
            guidance.get(response.status_code, "The selected free model may be unavailable."))
    try:
        body = response.json()
        choice = body["choices"][0]
        message = choice["message"]
        if choice.get("finish_reason") != "stop" or message.get("refusal"):
            raise ValueError("Incomplete or refused response")
        result = json.loads(message["content"])
        if not isinstance(result, dict):
            raise ValueError("Expected an object")
    except (ValueError, KeyError, IndexError, TypeError):
        raise ReasoningAPIError("OpenRouter returned an incomplete or invalid JSON decision.") from None
    return result
