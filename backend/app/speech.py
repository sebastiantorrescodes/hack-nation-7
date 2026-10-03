"""ElevenLabs speech transport; no workflow reasoning belongs here."""

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv


class SpeechError(RuntimeError):
    """Safe provider error without credentials, transcripts, or raw response bodies."""


class ElevenLabsSpeech:
    def __init__(self, *, transport=None):
        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
        self.key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        self.voice = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb").strip()
        self.transport = transport

    async def _request(self, method, path, **kwargs):
        if not self.key:
            raise SpeechError("Set ELEVENLABS_API_KEY locally first.")
        try:
            async with httpx.AsyncClient(timeout=60, transport=self.transport) as client:
                response = await client.request(method, "https://api.elevenlabs.io/v1/" + path,
                    headers={"xi-api-key": self.key}, **kwargs)
        except httpx.RequestError:
            raise SpeechError("ElevenLabs connection failed.") from None
        if response.status_code != 200:
            raise SpeechError(f"ElevenLabs HTTP {response.status_code}; check endpoint permissions and available credits.")
        return response

    async def speak(self, text: str) -> bytes:
        if not text.strip() or len(text) > 300:
            raise ValueError("Speech must be one question of at most 300 characters.")
        response = await self._request("POST", f"text-to-speech/{self.voice}",
            params={"output_format": "mp3_44100_128"},
            json={"text": text, "model_id": "eleven_flash_v2_5"})
        if not response.headers.get("content-type", "").startswith("audio/") or not response.content:
            raise SpeechError("ElevenLabs did not return audio.")
        return response.content

    async def transcribe(self, audio: bytes, content_type: str) -> str:
        if not audio or len(audio) > 5_000_000:
            raise ValueError("Record a short answer under 5 MB.")
        response = await self._request("POST", "speech-to-text",
            data={"model_id": "scribe_v2", "tag_audio_events": "false", "diarize": "false"},
            files={"file": ("answer.webm", audio, content_type)})
        try:
            text = response.json()["text"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise SpeechError("No speech was transcribed; record another answer.") from None
        return text
