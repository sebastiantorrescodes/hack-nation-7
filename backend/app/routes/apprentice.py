"""Bounded interview lifecycle behind the same capture coordinator and evidence store."""

import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator

from .. import store
from ..agent import interview, persistence, tools
from ..agent.schemas import CaptureContext
from ..models import TranscriptTurn
from ..speech import ElevenLabsSpeech
from .capture import FrameBody, LinkedTurn, _get, _workflow, add_turn, analyze_frame

router = APIRouter(prefix='/api/capture/sessions/{session_id}/apprentice', tags=['apprentice'])


async def load(session_id):
    session = await _get(session_id)
    state, revision = await persistence.load(session.id, session.expert_name)
    return session,state,revision


class EventsBody(FrameBody):
    t: float = 0


@router.get('')
async def get_state(session_id: str):
    _,state,_ = await load(session_id)
    return state


@router.post('/events')
async def events(session_id: str, body: EventsBody):
    session = await _get(session_id)
    event = await analyze_frame(session_id, FrameBody(client_id=body.client_id,t=max(0,time.time()-session.started_at),
        events=body.events,context=body.context,page=body.page,reasoning_provider='qwen'))
    _,state,_ = await load(session_id)
    return {'state':state,'event':event}


@router.post('/advance')
async def advance(session_id: str, body: EventsBody):
    session = await _get(session_id)
    # Follow-up reasoning uses already committed facts. It does not invent a new screen action.
    return await interview.analyze(session,await _workflow(session),FrameBody(
        t=max(0,time.time()-session.started_at),page=body.page,context=body.context,reasoning_provider='qwen'))


class DeliveryBody(BaseModel):
    confirmed: bool
    segment_id: int | None = None


@router.post('/questions/{question_id}/delivery')
async def delivery(session_id: str, question_id: str, body: DeliveryBody):
    session,state,revision = await load(session_id)
    question = tools.pending_question(state)
    if not body.confirmed or not question or question.id!=question_id:
        raise HTTPException(409,'Confirm the pending question first.')
    rows = (await store._t('transcript_segments').select('*').eq('session_id',session_id)
            .eq('speaker','agent').eq('off_record',False).order('id',desc=True).limit(1).execute()).data
    if not rows or (body.segment_id is not None and rows[0]['id']!=body.segment_id):
        raise HTTPException(409,'There is no recorded agent turn to confirm.')
    # Human reconciliation allows paraphrasing without a second LLM guessing delivery.
    if question_id not in state.delivered_question_ids: state.delivered_question_ids.append(question_id)
    state.delivery_segments[question_id] = rows[0]["id"]
    await persistence.save(session,state,revision)
    return {'ok':True,'segment_id':rows[0]['id']}


class LinkBody(BaseModel):
    segment_id: int


@router.post('/questions/{question_id}/link-answer')
async def link_answer(session_id: str, question_id: str, body: LinkBody):
    session,_,_ = await load(session_id)
    rows = (await store._t('transcript_segments').select('*').eq('session_id',session_id)
        .eq('id',body.segment_id).eq('speaker','expert').eq('off_record',False).execute()).data
    if not rows: raise HTTPException(404,'On-record expert answer not found.')
    row=rows[0]
    await interview.link_turn(session,LinkedTurn(client_id=row['client_id'],segment_id=row['id'],
        t=(row['t_start_ms'] or 0)/1000,role='expert',text=row['text'],question_id=question_id))
    return {'ok':True}


@router.get('/questions/{question_id}/audio')
async def audio(session_id: str, question_id: str):
    _,state,_ = await load(session_id)
    question = tools.pending_question(state)
    if not question or question.id!=question_id: raise HTTPException(409,'Question is no longer pending.')
    # Returning audio does not mark it as delivered; the UI confirms actual playback.
    return Response(await ElevenLabsSpeech().speak(question.text),media_type='audio/mpeg',headers={'Cache-Control':'no-store'})


@router.post('/questions/{question_id}/answer')
async def answer(session_id: str, question_id: str, request: Request):
    session,state,_ = await load(session_id)
    question = tools.pending_question(state)
    if not question or question.id != question_id: raise HTTPException(409,'Question is no longer pending.')
    if question_id not in state.delivered_question_ids: raise HTTPException(409,'Confirm question delivery before answering.')
    content_type=request.headers.get('content-type','').split(';')[0]
    if content_type not in {'audio/webm','audio/ogg','audio/mp4','audio/wav'}: raise HTTPException(415,'Unsupported recording format.')
    data=bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data)>5_000_000: raise HTTPException(413,'Recording exceeds 5 MB.')
    if not data: raise HTTPException(400,'Recording is empty.')
    text=await ElevenLabsSpeech().transcribe(bytes(data),content_type)
    return await add_turn(session_id,LinkedTurn(client_id=uuid4().hex,t=max(0,time.time()-session.started_at),
        role='expert',text=text,question_id=question_id))
