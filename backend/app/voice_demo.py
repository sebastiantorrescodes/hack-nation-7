"""Standalone Qwen + ElevenLabs demo, independent of legacy Claude capture."""

import asyncio
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

from .agent.apprentice import process_event
from .agent.schemas import CaptureContext, WorkMapState
from .agent.tools import pending_question, record_expert_answer
from .api_llm import ReasoningAPIError
from .events.normalizer import normalize_browser_event
from .speech import ElevenLabsSpeech, SpeechError


app = FastAPI(title="AI Apprentice voice demo")
sessions: dict[str, WorkMapState] = {}
locks: dict[str, asyncio.Lock] = {}


@app.exception_handler(SpeechError)
@app.exception_handler(ReasoningAPIError)
async def provider_error(request, error):
    return JSONResponse(status_code=502, content={"detail": str(error)})


def state_for(session_id):
    if session_id not in sessions:
        raise HTTPException(404, "Session not found; start a new demo.")
    return sessions[session_id]


async def reason(state, event):
    try:
        return await process_event(event, state, CaptureContext(expert_paused=True))
    except (ValidationError, ValueError):
        raise HTTPException(422, "Model decision failed agent validation; no new tool action executed.") from None


@app.get("/")
async def page():
    return FileResponse(Path(__file__).with_name("voice_demo.html"))


class Start(BaseModel):
    expert_name: str = Field(default="Demo expert", min_length=1, max_length=80)


@app.post("/api/demo/sessions")
async def start(body: Start):
    if len(sessions) >= 100:
        raise HTTPException(503, "Demo session capacity reached; restart the local server.")
    session_id = uuid4().hex
    state = WorkMapState(expert_name=body.expert_name)
    event = normalize_browser_event({"event_id": uuid4().hex, "event_type": "field_change",
        "target": "Review status", "old_value": "Pending", "new_value": "Needs review",
        "page": "Synthetic workflow demo"})
    decision = await reason(state, event)
    sessions[session_id] = state
    locks[session_id] = asyncio.Lock()
    return {"id": session_id, "decision": decision, "state": state}


@app.get("/api/demo/sessions/{session_id}")
async def get_state(session_id: str):
    return state_for(session_id)


@app.get("/api/demo/sessions/{session_id}/questions/{question_id}/audio")
async def question_audio(session_id: str, question_id: str):
    state = state_for(session_id)
    question = pending_question(state)
    if question is None or question.id != question_id:
        raise HTTPException(409, "This question is no longer awaiting an answer.")
    audio = await ElevenLabsSpeech().speak(question.text)
    return Response(audio, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})


@app.post("/api/demo/sessions/{session_id}/questions/{question_id}/answer")
async def answer(session_id: str, question_id: str, request: Request):
    state = state_for(session_id)
    async with locks[session_id]:
        question = pending_question(state)
        if question is None or question.id != question_id:
            raise HTTPException(409, "Question is no longer pending; answer was not submitted.")
        content_type = request.headers.get("content-type", "").split(";")[0]
        if content_type not in {"audio/webm", "audio/ogg", "audio/mp4", "audio/wav"}:
            raise HTTPException(415, "Unsupported recording format.")
        audio = bytearray()
        async for chunk in request.stream():
            audio.extend(chunk)
            if len(audio) > 5_000_000:
                raise HTTPException(413, "Answer exceeds 5 MB.")
        if not audio:
            raise HTTPException(400, "No recording supplied.")
        text = await ElevenLabsSpeech().transcribe(bytes(audio), content_type)
        evidence = record_expert_answer(state, question_id, text)
        # Preserve a successfully recorded answer even if the following Qwen call fails.
        try:
            decision = await reason(state, state.events[question.event_id])
            error = None
        except (ReasoningAPIError, HTTPException):
            decision = None
            error = "Answer retained. Qwen could not make the next decision; use Retry reasoning."
        return {"answer": evidence, "decision": decision, "state": state, "error": error}


@app.post("/api/demo/sessions/{session_id}/reason")
async def retry(session_id: str):
    state = state_for(session_id)
    async with locks[session_id]:
        decision = await reason(state, next(reversed(state.events.values())))
        return {"decision": decision, "state": state}
