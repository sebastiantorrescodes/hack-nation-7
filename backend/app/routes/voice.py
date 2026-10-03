"""ElevenLabs Conversational AI: hand the browser a short-lived signed URL so the API key stays server-side."""

import httpx
from fastapi import APIRouter, HTTPException

from ..config import ELEVENLABS_AGENT_ID, ELEVENLABS_API_KEY

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.get("/signed-url")
async def signed_url():
    if not ELEVENLABS_API_KEY or not ELEVENLABS_AGENT_ID:
        raise HTTPException(500, "Set ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID in backend/.env")
    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.get(
            "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url",
            params={"agent_id": ELEVENLABS_AGENT_ID},
            headers={"xi-api-key": ELEVENLABS_API_KEY},
        )
    if r.status_code != 200:
        raise HTTPException(502, f"ElevenLabs error {r.status_code}: {r.text}")
    return {"signed_url": r.json()["signed_url"]}
