"""One structured model boundary for Gemini and zero-price OpenRouter requests.

Gemini must use a Google Free tier project: unlike OpenRouter, its API cannot
enforce zero-price routing on a billing-enabled project.
"""

import json
import os
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from dotenv import load_dotenv


DEFAULT_MODEL = "qwen/qwen3.8-27b:free"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"
# Standard inference on these models is listed in Google's Free tier pricing.
# This is a model restriction, not a check of the Google project's billing tier.
GEMINI_FREE_TIER_MODELS = frozenset({DEFAULT_GEMINI_MODEL, "gemini-3.8-flash", "gemini-3.1-flash-lite"})


class ReasoningAPIError(RuntimeError):
    """Report configuration or provider failure without logging private content."""


def configured_model() -> tuple[str, str]:
    """Resolve the provider without returning credentials or inspecting them in logs.

    Existing OpenRouter installations keep working. Adding a Gemini key selects
    Gemini unless LLM_PROVIDER explicitly chooses a provider. Failures never switch
    providers automatically or resend private context to a second service.
    """
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    provider = os.getenv("LLM_PROVIDER", "auto").strip().lower() or "auto"
    if provider == "auto":
        provider = "gemini" if os.getenv("GEMINI_API_KEY", "").strip() else "openrouter"
    if provider == "gemini":
        model = os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
        if model not in GEMINI_FREE_TIER_MODELS:
            raise ReasoningAPIError("GEMINI_MODEL must be a supported Free tier Flash model; Pro and unlisted models are disabled.")
    elif provider == "openrouter":
        model = os.getenv("OPENROUTER_MODEL", DEFAULT_MODEL).strip()
        if not model.endswith(":free"):
            raise ReasoningAPIError("OPENROUTER_MODEL must end with :free; paid models are disabled.")
    else:
        raise ReasoningAPIError("LLM_PROVIDER must be auto, gemini or openrouter.")
    return provider, model


def _part(block: dict) -> dict:
    """Both providers accept the same OpenAI-compatible text/image blocks."""
    if block.get("type") == "image" and block.get("source", {}).get("type") == "base64":
        src = block["source"]
        return {"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}}
    return block


def _content(content: str | list[dict]) -> str | list[dict]:
    return content if isinstance(content, str) else [_part(b) for b in content]


async def structured(*, system: str, content: str | list[dict], schema: dict,
                     max_tokens: int = 3000, effort: str = "low",
                     transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Request schema-shaped JSON. `content` is a string or a list of text/image blocks."""
    provider, model = configured_model()
    key_name = "GEMINI_API_KEY" if provider == "gemini" else "OPENROUTER_API_KEY"
    key = os.getenv(key_name, "").strip()
    if not key:
        raise ReasoningAPIError(f"Set {key_name} in backend/.env first.")
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": _content(content)}],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "result", "strict": True, "schema": schema}},
    }
    if provider == "gemini":
        endpoint, label = GEMINI_ENDPOINT, "Gemini"
        payload["reasoning_effort"] = effort
    else:
        endpoint, label = ENDPOINT, "OpenRouter"
        payload["reasoning"] = {"effort": effort}
        payload["provider"] = {"require_parameters": True,
            "max_price": {"prompt": 0, "completion": 0, "request": 0, "image": 0}}
    try:
        # Live screen feedback has a shorter deadline than a whole-session build.
        timeout = httpx.Timeout(45 if effort == "low" else 180, connect=10)
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            response = await client.post(endpoint, json=payload,
                headers={"Authorization": f"Bearer {key}"})
    except httpx.TimeoutException:
        raise ReasoningAPIError("Reasoning timed out. Captured facts are retained; retry analysis or build from the saved interview.") from None
    except httpx.RequestError:
        raise ReasoningAPIError(f"{label} connection failed. Captured facts are retained.") from None
    if response.status_code != 200:
        guidance = {400: "The model rejected the request format; captured facts are retained.",
                    401: f"Check your {label} API key.",
                    403: "Check the API key's project access and restrictions.",
                    402: "Free capacity or account access is unavailable; do not add paid credits.",
                    404: "The configured model is not available for this account.",
                    429: "Free requests are rate limited; try again later. Captured facts are retained."}
        raise ReasoningAPIError(f"{label} HTTP {response.status_code}. " +
            guidance.get(response.status_code, "The selected model may be unavailable."))
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
        raise ReasoningAPIError(f"{label} returned an incomplete or invalid JSON response.") from None
    try:
        Draft202012Validator(schema).validate(result)
    except ValidationError:
        raise ReasoningAPIError(f"{label} returned JSON that failed the requested schema. No decision was applied.") from None
    return result
