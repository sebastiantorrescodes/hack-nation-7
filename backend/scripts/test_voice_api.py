"""Check ElevenLabs access without generating speech or printing credentials."""

import asyncio
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


async def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    agent_id = os.getenv("ELEVENLABS_AGENT_ID", "").strip()
    if not key:
        print("ElevenLabs: API key is not configured.")
        return 1
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get("https://api.elevenlabs.io/v2/voices",
                params={"page_size": 1}, headers={"xi-api-key": key})
            print(f"ElevenLabs voice access: HTTP {response.status_code}.")
            if response.status_code != 200:
                print("Check the key's voice read permission and account access.")
                return 1
            if not agent_id:
                print("Conversation test skipped: ELEVENLABS_AGENT_ID is not configured.")
                return 0
            response = await client.get(
                "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
                params={"agent_id": agent_id}, headers={"xi-api-key": key})
            print(f"ElevenLabs conversation access: HTTP {response.status_code}.")
            if response.status_code != 200:
                # Report only recognized diagnostic labels, never the raw provider body.
                try:
                    detail = response.json().get("detail", {})
                    diagnostic = str(detail).lower()
                    for label in ("missing_permissions", "invalid_api_key", "agent_not_found"):
                        if label in diagnostic:
                            print(f"Conversation diagnostic: {label}.")
                    for permission in ("convai_read", "convai_write"):
                        if permission in diagnostic:
                            print(f"Permission mentioned by provider: {permission}.")
                except (ValueError, AttributeError):
                    pass
                return 1
            if not isinstance(response.json().get("signed_url"), str):
                print("Conversation response did not contain a signed URL.")
                return 1
            print("Conversation signed URL received (kept private); no voice session started.")
            return 0
    except (httpx.RequestError, ValueError, AttributeError):
        print("ElevenLabs connection or response check failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
