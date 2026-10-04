"""Optional Qwen interview decisions behind the existing conversational capture API."""

import json

from . import persistence, tools
from .apprentice import process_event
from ..api_llm import structured
from ..events.normalizer import normalize_browser_event
from ..models import ScreenEvent


def question_matches(actual: str, requested: str) -> bool:
    """Only whitespace/case differences count as the same delivered question."""
    return " ".join(actual.split()).casefold() == " ".join(requested.split()).casefold()


async def analyze(session, workflow, body):
    state, revision = await persistence.load(session.id, session.expert_name)
    fresh = []
    for payload in body.events:
        event = normalize_browser_event(payload)
        if event.event_id in state.events:
            if state.events[event.event_id] != event:
                raise ValueError("Conflicting event ID")
        else:
            state.events[event.event_id] = event
            fresh.append(event)
    if fresh:
        revision = await persistence.save(session, state, revision)
    if not state.events:
        return None

    async def reasoning(**kwargs):
        content = json.loads(kwargs["content"])
        content["workflow"] = workflow.model_dump(mode="json")
        content["page_snapshot"] = body.page[:40000]
        content["recent_transcript"] = [turn.model_dump() for turn in session.transcript[-6:]]
        # Only linked expert answers in workmap.answers can support proposed knowledge.
        kwargs["content"] = json.dumps(content, ensure_ascii=False)
        return await structured(**kwargs)

    decision = await process_event(next(reversed(state.events.values())), state, body.context, reason=reasoning)
    await persistence.save(session, state, revision)
    if decision.action != "ask_question":
        pending = tools.pending_question(state)
        if (not pending or pending.id in state.delivered_question_ids or not body.context.expert_paused
                or body.context.expert_busy or body.context.expert_speaking):
            return None
    question = tools.pending_question(state)
    event = state.events[question.event_id]
    return ScreenEvent(t=body.t, kind=event.event_type,
        description=event.description or f"{event.event_type}: {event.target or event.page}",
        is_decision_point=True, ask_why=question.text, question_id=question.id)


async def link_turn(session, turn):
    """Record verified delivery or an expert response without guessing a question ID."""
    if not turn.question_id:
        return
    state, revision = await persistence.load(session.id, session.expert_name)
    if turn.role == "expert" and turn.segment_id is not None and any(a.segment_id == turn.segment_id and a.question_id == turn.question_id for a in state.answers):
        return
    question = tools.pending_question(state)
    if not question or question.id != turn.question_id:
        raise ValueError("Question is not pending")
    if turn.role == "agent":
        if not question_matches(turn.text, question.text):
            raise ValueError("Voice transcript does not match the requested question")
        if question.id not in state.delivered_question_ids:
            state.delivered_question_ids.append(question.id)
        if turn.segment_id is not None: state.delivery_segments[question.id] = turn.segment_id
    else:
        if question.id not in state.delivered_question_ids:
            raise ValueError("Question delivery has not been verified")
        answer = tools.record_expert_answer(state, question.id, turn.text)
        state.answers[-1].segment_id = turn.segment_id
    await persistence.save(session, state, revision)
