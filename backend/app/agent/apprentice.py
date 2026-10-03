# This file asks the reasoning LLM for one bounded decision and executes only explicit tools.

from collections.abc import Awaitable, Callable

from ..events.schemas import ObservedEvent
from . import tools
from .prompts import SYSTEM_PROMPT, decision_content, decision_schema
from .schemas import AgentDecision, CaptureContext, WorkMapState


ReasoningCall = Callable[..., Awaitable[dict]]


def _simple_decision(action: str, reason: str) -> AgentDecision:
    """Return a policy decision without any model-controlled tool arguments."""
    return AgentDecision(action=action, reason=reason, question=None, step=None, step_id=None)


def apply_decision(decision: AgentDecision, state: WorkMapState, context: CaptureContext) -> None:
    """Dispatch a validated decision to the finite tool set, with policy checks."""
    if state.phase == "finished":
        raise ValueError("Session is finished")
    if decision.action == "wait":
        return
    if decision.action in {"ask_question", "start_debrief", "finish_session"}:
        if not context.expert_paused or context.expert_busy or context.expert_speaking:
            raise ValueError("Wait for a natural pause before interrupting the expert")
    if decision.action == "ask_question":
        tools.ask_expert(state, decision.question)
    elif decision.action == "save_step":
        tools.save_workmap_step(state, decision.step)
    elif decision.action == "update_step":
        tools.update_workmap_step(state, decision.step_id, decision.step)
    elif decision.action == "start_debrief":
        if not context.task_finished:
            raise ValueError("The task has not finished")
        tools.start_debrief(state)
    elif decision.action == "finish_session":
        tools.finish_session(state)
    else:
        raise ValueError("Unsupported agent action")


async def process_event(
    event: ObservedEvent,
    state: WorkMapState,
    context: CaptureContext,
    *,
    reason: ReasoningCall | None = None,
) -> AgentDecision:
    """Record an event, wait during activity, or execute one reasoning decision."""
    if state.phase == "finished":
        return _simple_decision("wait", "Session is already finished")
    previous = state.events.get(event.event_id)
    if previous is not None and previous != event:
        raise ValueError("Event ID was reused for different screen facts")
    state.events[event.event_id] = event.model_copy(deep=True)
    if context.expert_busy or context.expert_speaking or not context.expert_paused:
        return _simple_decision("wait", "Wait for a reported natural pause")
    if tools.pending_question(state):
        return _simple_decision("wait", "Wait for the expert's answer")
    if context.task_finished and state.phase == "capture":
        decision = _simple_decision("start_debrief", "Task finished; review missing knowledge")
    else:
        if reason is None:
            # Keep SDK imports out of offline tests; use free-only API reasoning by default.
            from ..api_llm import structured
            reason = structured
        result = await reason(
            system=SYSTEM_PROMPT, content=decision_content(event, state, context),
            schema=decision_schema(), max_tokens=3000, effort="low",
        )
        decision = AgentDecision.model_validate(result)
    # Invalid or unsupported model actions fail without executing arbitrary tools.
    apply_decision(decision, state, context)
    return decision
