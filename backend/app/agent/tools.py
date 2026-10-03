# This file provides the apprentice's explicit in-memory tools and expert-only review operations.

from .schemas import ExpertAnswer, ExpertQuestion, QuestionRequest, StepProposal, WorkflowStep, WorkMapState


def _active(state: WorkMapState) -> None:
    """Reject writes after the session has finished."""
    if state.phase == "finished":
        raise ValueError("Session is finished")


def _step(state: WorkMapState, step_id: str) -> WorkflowStep:
    """Find a recorded step or reject an unknown identifier."""
    for step in state.steps:
        if step.id == step_id:
            return step
    raise ValueError("Unknown workflow step")


def pending_question(state: WorkMapState) -> ExpertQuestion | None:
    """Return an unanswered question so capture waits for the expert."""
    answered = {answer.question_id for answer in state.answers}
    return next((question for question in state.questions if question.id not in answered), None)


def ask_expert(state: WorkMapState, request: QuestionRequest) -> ExpertQuestion:
    """Queue one event-grounded question; voice delivery belongs to Step 4."""
    _active(state)
    if pending_question(state):
        raise ValueError("Wait for the expert's pending answer")
    if request.event_id not in state.events or not request.text.strip():
        raise ValueError("Question requires a recorded event and non-empty text")
    if any(q.event_id == request.event_id and q.text.strip().casefold() == request.text.strip().casefold()
           for q in state.questions):
        raise ValueError("This question has already been asked")
    question = ExpertQuestion(**request.model_dump(), phase=state.phase)
    state.questions.append(question)
    state.teach_back_confirmed_by = None
    return question.model_copy(deep=True)


def record_expert_answer(state: WorkMapState, question_id: str, text: str) -> ExpertAnswer:
    """Accept an actual expert answer from the application, never from the LLM."""
    _active(state)
    question = pending_question(state)
    if question is None or question.id != question_id or not text.strip():
        raise ValueError("Answer requires the pending question and non-empty expert text")
    answer = ExpertAnswer(question_id=question_id, event_id=question.event_id, text=text)
    state.answers.append(answer)
    state.teach_back_confirmed_by = None
    return answer.model_copy(deep=True)


def _check_evidence(state: WorkMapState, proposal: StepProposal) -> None:
    """Reject fabricated event/answer IDs and mismatched evidence references."""
    if any(event_id not in state.events for event_id in proposal.event_ids):
        raise ValueError("Step references an unrecorded event")
    answers = {answer.id: answer for answer in state.answers}
    if any(answer_id not in answers for answer_id in proposal.answer_ids):
        raise ValueError("Step references an unrecorded expert answer")
    if any(answers[answer_id].event_id not in proposal.event_ids for answer_id in proposal.answer_ids):
        raise ValueError("Expert answer does not belong to the step's recorded events")
    if not proposal.title.strip():
        raise ValueError("Step title must not be blank")


def save_workmap_step(state: WorkMapState, proposal: StepProposal) -> WorkflowStep:
    """Save evidence-backed knowledge as an unconfirmed proposal."""
    _active(state)
    proposal = StepProposal.model_validate(proposal.model_dump())
    _check_evidence(state, proposal)
    if any(StepProposal.model_validate(step.model_dump(include=set(StepProposal.model_fields))) == proposal
           for step in state.steps):
        raise ValueError("This workflow proposal has already been saved")
    step = WorkflowStep(**proposal.model_dump())
    state.steps.append(step)
    state.teach_back_confirmed_by = None
    return step.model_copy(deep=True)


def update_workmap_step(state: WorkMapState, step_id: str, proposal: StepProposal) -> WorkflowStep:
    """Replace a proposal and invalidate any earlier expert confirmation."""
    _active(state)
    proposal = StepProposal.model_validate(proposal.model_dump())
    previous = _step(state, step_id)
    _check_evidence(state, proposal)
    updated = WorkflowStep(id=step_id, **proposal.model_dump())
    state.steps[state.steps.index(previous)] = updated
    state.teach_back_confirmed_by = None
    return updated.model_copy(deep=True)


def get_workmap(state: WorkMapState) -> WorkMapState:
    """Read a copy of the Work Map so callers cannot mutate it through a read."""
    return state.model_copy(deep=True)


def get_missing_fields(state: WorkMapState) -> dict[str, list[str]]:
    """List incomplete actions, reasons, guardrails and expert confirmations."""
    missing = {}
    for step in state.steps:
        fields = []
        for field in ("action", "reason"):
            value = getattr(step, field)
            if value is None or not value.strip():
                fields.append(field)
        if step.guardrails is None or any(not guardrail.strip() for guardrail in step.guardrails):
            fields.append("guardrails")
        if step.status != "confirmed" or step.confirmed_by != state.expert_name:
            fields.append("expert_confirmation")
        if fields:
            missing[step.id] = fields
    return missing


def start_debrief(state: WorkMapState) -> None:
    """Start the debrief once capture ends and the current answer is complete."""
    _active(state)
    if state.phase != "capture" or pending_question(state):
        raise ValueError("Debrief requires capture phase and no pending answer")
    state.phase = "debrief"


def confirm_step(state: WorkMapState, step_id: str, expert_name: str) -> WorkflowStep:
    """Record explicit expert approval; this operation is unavailable to the LLM."""
    _active(state)
    step = _step(state, step_id)
    if expert_name != state.expert_name:
        raise ValueError("Confirmation must come from this session's expert")
    missing = get_missing_fields(state).get(step_id, [])
    if any(field != "expert_confirmation" for field in missing):
        raise ValueError("Complete the step's missing knowledge before confirming")
    step.status, step.confirmed_by = "confirmed", expert_name
    state.teach_back_confirmed_by = None
    return step.model_copy(deep=True)


def confirm_teach_back(state: WorkMapState, expert_name: str) -> None:
    """Record the expert's approval after hearing the debrief's teach-back."""
    if (state.phase != "debrief" or expert_name != state.expert_name
            or not state.steps or get_missing_fields(state) or pending_question(state)):
        raise ValueError("Teach-back requires a complete, expert-confirmed Work Map and no pending answer")
    state.teach_back_confirmed_by = expert_name


def finish_session(state: WorkMapState) -> None:
    """Finish only after all knowledge and the teach-back are expert-confirmed."""
    if (state.phase != "debrief" or not state.steps or get_missing_fields(state)
            or pending_question(state) or state.teach_back_confirmed_by != state.expert_name):
        raise ValueError("Expert-confirmed steps and teach-back are required to finish")
    state.phase = "finished"
