# This file tests the apprentice's bounded decisions and expert confirmation with a fake LLM.

import json
import unittest
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.agent.apprentice import apply_decision, process_event
from app.agent.prompts import decision_schema
from app.agent.schemas import AgentDecision, CaptureContext, QuestionRequest, StepProposal, WorkflowStep, WorkMapState
from app.agent import tools
from app.events.normalizer import normalize_browser_event


def screen_event(event_id="event-1"):
    """Create a direct browser field change with known provenance."""
    return normalize_browser_event({
        "event_id": event_id, "event_type": "field_change", "target": "Modifier",
        "old_value": "", "new_value": "25", "page": "Billing",
    })


def decision(action="wait", **arguments):
    """Return the complete shape expected from the reasoning LLM."""
    return {"action": action, "reason": "Test decision", "question": None,
            "step": None, "step_id": None, **arguments}


def question(event_id="event-1"):
    """Ask for expert reasoning rather than what the screen already shows."""
    return {"event_id": event_id, "text": "What told you this modifier was needed?",
            "question_type": "reason"}


def evidence_state():
    """Record a real application-supplied answer without calling a model."""
    event = screen_event()
    state = WorkMapState(expert_name="Vincent", events={event.event_id: event})
    queued = tools.ask_expert(state, QuestionRequest.model_validate(question()))
    answer = tools.record_expert_answer(state, queued.id, "I checked the documented separate service.")
    return state, event, answer


def proposal(answer_id):
    """Reference a recorded answer in a complete, still-unconfirmed proposal."""
    return {"title": "Review the modifier", "action": "Review the documented separate service",
            "reason": "I checked the documented separate service.", "guardrails": [],
            "event_ids": ["event-1"], "answer_ids": [answer_id]}


class ApprenticeTests(unittest.IsolatedAsyncioTestCase):
    """Exercise one-decision processing without credentials or network calls."""

    async def test_activity_and_unknown_pause_wait_without_calling_llm(self):
        """Retain screen events while staying quiet during typing, reading or speech."""
        for context in [CaptureContext(), CaptureContext(expert_paused=True, expert_busy=True),
                        CaptureContext(expert_paused=True, expert_speaking=True)]:
            with self.subTest(context=context):
                state = WorkMapState(expert_name="Vincent")
                llm = AsyncMock()
                result = await process_event(screen_event(), state, context, reason=llm)
                self.assertEqual(result.action, "wait")
                self.assertIn("event-1", state.events)
                self.assertEqual(state.questions, [])
                llm.assert_not_awaited()

    async def test_question_pending_answer_then_save_proposal(self):
        """Ask at a pause, wait for a real answer, then save evidence-backed knowledge."""
        state = WorkMapState(expert_name="Vincent")
        context = CaptureContext(expert_paused=True)
        llm = AsyncMock(return_value=decision("ask_question", question=question()))
        result = await process_event(screen_event(), state, context, reason=llm)
        self.assertEqual(result.action, "ask_question")
        self.assertEqual(len(state.questions), 1)
        content = json.loads(llm.call_args.kwargs["content"])
        self.assertEqual(content["event"]["event_id"], "event-1")
        self.assertEqual(content["workmap"]["expert_name"], "Vincent")
        llm.reset_mock()
        waiting = await process_event(screen_event("event-2"), state, context, reason=llm)
        self.assertEqual(waiting.action, "wait")
        llm.assert_not_awaited()
        answer = tools.record_expert_answer(state, state.questions[0].id,
                                           "I checked the documented separate service.")
        llm.return_value = decision("save_step", step=proposal(answer.id))
        await process_event(screen_event(), state, context, reason=llm)
        self.assertEqual(state.steps[0].status, "proposed")
        self.assertIsNone(state.steps[0].confirmed_by)
        self.assertEqual(state.steps[0].answer_ids, [answer.id])

    async def test_task_finished_starts_debrief_without_model_claim(self):
        """Use the application's task-end signal, never a model's guessed completion."""
        state, event, _ = evidence_state()
        llm = AsyncMock()
        result = await process_event(event, state, CaptureContext(expert_paused=True, task_finished=True), reason=llm)
        self.assertEqual(result.action, "start_debrief")
        self.assertEqual(state.phase, "debrief")
        llm.assert_not_awaited()

    async def test_complete_expert_review_and_finish(self):
        """Require step approval and a separate teach-back approval before finishing."""
        state, event, answer = evidence_state()
        saved = tools.save_workmap_step(state, StepProposal.model_validate(proposal(answer.id)))
        tools.confirm_step(state, saved.id, "Vincent")
        tools.start_debrief(state)
        tools.confirm_teach_back(state, "Vincent")
        llm = AsyncMock(return_value=decision("finish_session"))
        result = await process_event(event, state, CaptureContext(expert_paused=True), reason=llm)
        self.assertEqual(result.action, "finish_session")
        self.assertEqual(state.phase, "finished")
        llm.reset_mock()
        await process_event(event, state, CaptureContext(expert_paused=True), reason=llm)
        llm.assert_not_awaited()

    async def test_invalid_model_actions_and_payloads_have_no_tool_effects(self):
        """Reject system execution, invented approvals and mismatched tool arguments."""
        for response in [decision("run_shell"), decision("confirm_step"),
                         decision("ask_question"), decision("wait", question=question()),
                         decision("wait", confirmed=True)]:
            state = WorkMapState(expert_name="Vincent")
            with self.subTest(response=response), self.assertRaises(ValidationError):
                await process_event(screen_event(), state, CaptureContext(expert_paused=True),
                                    reason=AsyncMock(return_value=response))
            self.assertEqual(state.questions, [])
            self.assertEqual(state.steps, [])

    async def test_failed_llm_does_not_create_question_or_knowledge(self):
        """Propagate provider failures without inventing a successful decision."""
        state = WorkMapState(expert_name="Vincent")
        with self.assertRaises(RuntimeError):
            await process_event(screen_event(), state, CaptureContext(expert_paused=True),
                                reason=AsyncMock(side_effect=RuntimeError("provider unavailable")))
        self.assertEqual(state.steps, [])
        self.assertEqual(state.questions, [])

    async def test_conflicting_event_id_is_rejected(self):
        """Prevent a replayed identifier from silently changing evidence."""
        state, _, _ = evidence_state()
        changed = screen_event().model_copy(update={"new_value": "59"})
        with self.assertRaisesRegex(ValueError, "reused"):
            await process_event(changed, state, CaptureContext(expert_paused=True), reason=AsyncMock())
        self.assertEqual(state.events["event-1"].new_value, "25")

    async def test_resumed_activity_while_llm_runs_prevents_question(self):
        """Recheck the caller's current activity before queuing model output."""
        state = WorkMapState(expert_name="Vincent")
        context = CaptureContext(expert_paused=True)

        async def resumed_activity(**kwargs):
            """Simulate the expert starting to type during the reasoning request."""
            context.expert_busy = True
            return decision("ask_question", question=question())

        with self.assertRaises(ValueError):
            await process_event(screen_event(), state, context, reason=resumed_activity)
        self.assertEqual(state.questions, [])


class ToolPolicyTests(unittest.TestCase):
    """Check policy at the dispatcher and evidence boundaries, not just in prompts."""

    def test_dispatcher_enforces_pause_and_task_end(self):
        """Reject interruption and premature debrief even when a model requests them."""
        state, _, _ = evidence_state()
        for context in [CaptureContext(), CaptureContext(expert_paused=True, expert_busy=True),
                        CaptureContext(expert_paused=True, expert_speaking=True)]:
            with self.subTest(context=context), self.assertRaises(ValueError):
                apply_decision(AgentDecision.model_validate(decision("ask_question", question=question())), state, context)
        with self.assertRaises(ValueError):
            apply_decision(AgentDecision.model_validate(decision("start_debrief")), state,
                           CaptureContext(expert_paused=True))
        self.assertEqual(state.phase, "capture")

    def test_api_schema_omits_unsupported_lengths_but_local_validation_keeps_them(self):
        """Keep API schema compatibility without weakening returned-payload validation."""
        schema = json.dumps(decision_schema())
        self.assertNotIn('"minLength"', schema)
        self.assertNotIn('"maxLength"', schema)
        self.assertIn('"additionalProperties": false', schema)
        with self.assertRaises(ValidationError):
            AgentDecision.model_validate(decision("ask_question", question={**question(), "text": "x" * 301}))

    def test_questions_need_known_event_and_do_not_repeat(self):
        """Reject invented events and an exact question replay."""
        state, _, _ = evidence_state()
        for request in [question("invented-event"), question(), {**question(), "text": "   "}]:
            with self.subTest(request=request), self.assertRaises(ValueError):
                tools.ask_expert(state, QuestionRequest.model_validate(request))

    def test_answers_must_come_from_application_for_pending_question(self):
        """Retain exact expert text and reject missing or already-answered questions."""
        state = WorkMapState(expert_name="Vincent", events={"event-1": screen_event()})
        queued = tools.ask_expert(state, QuestionRequest.model_validate(question()))
        with self.assertRaises(ValueError):
            tools.record_expert_answer(state, "unknown", "Answer")
        answer = tools.record_expert_answer(state, queued.id, "  Expert's own words.  ")
        self.assertEqual(answer.text, "  Expert's own words.  ")
        with self.assertRaises(ValueError):
            tools.record_expert_answer(state, queued.id, "Second answer")

    def test_save_requires_real_related_evidence_and_no_confirmed_flag(self):
        """Reject fabricated IDs, unrelated answers and model-supplied confirmation."""
        state, _, answer = evidence_state()
        state.events["event-2"] = screen_event("event-2")
        for changes in [{"event_ids": ["invented"]}, {"answer_ids": ["invented"]},
                        {"event_ids": ["event-2"]}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                tools.save_workmap_step(state, StepProposal.model_validate({**proposal(answer.id), **changes}))
        with self.assertRaises(ValidationError):
            StepProposal.model_validate({**proposal(answer.id), "status": "confirmed"})
        with self.assertRaises(ValidationError):
            tools.save_workmap_step(state, WorkflowStep(**proposal(answer.id), status="confirmed", confirmed_by="Vincent"))
        self.assertEqual(state.steps, [])

    def test_missing_fields_require_completion_before_confirmation(self):
        """Differentiate an unknown guardrail from an explicit report of none."""
        state, _, answer = evidence_state()
        partial = StepProposal.model_validate({**proposal(answer.id), "reason": None, "guardrails": None})
        saved = tools.save_workmap_step(state, partial)
        self.assertEqual(tools.get_missing_fields(state)[saved.id], ["reason", "guardrails", "expert_confirmation"])
        with self.assertRaises(ValueError):
            tools.confirm_step(state, saved.id, "Vincent")
        updated = tools.update_workmap_step(state, saved.id, StepProposal.model_validate(proposal(answer.id)))
        with self.assertRaises(ValueError):
            tools.confirm_step(state, updated.id, "Other person")
        tools.confirm_step(state, updated.id, "Vincent")
        self.assertEqual(tools.get_missing_fields(state), {})

    def test_confirmed_status_without_expert_identity_is_still_missing_approval(self):
        """A loaded status flag alone cannot stand in for recorded expert confirmation."""
        state, _, answer = evidence_state()
        saved = tools.save_workmap_step(state, StepProposal.model_validate(proposal(answer.id)))
        state.steps[0].status = "confirmed"
        self.assertEqual(tools.get_missing_fields(state)[saved.id], ["expert_confirmation"])

    def test_update_invalidates_step_and_teachback_confirmation(self):
        """Edits to approved knowledge require fresh expert review."""
        state, _, answer = evidence_state()
        saved = tools.save_workmap_step(state, StepProposal.model_validate(proposal(answer.id)))
        tools.confirm_step(state, saved.id, "Vincent")
        tools.start_debrief(state)
        tools.confirm_teach_back(state, "Vincent")
        updated = tools.update_workmap_step(state, saved.id,
            StepProposal.model_validate({**proposal(answer.id), "title": "Corrected title"}))
        self.assertEqual(updated.id, saved.id)
        self.assertEqual(updated.status, "proposed")
        self.assertIsNone(updated.confirmed_by)
        self.assertIsNone(state.teach_back_confirmed_by)
        with self.assertRaises(ValueError):
            tools.finish_session(state)

    def test_read_returns_copy_and_duplicate_save_is_rejected(self):
        """Read access cannot mutate state or repeat an existing proposal."""
        state, _, answer = evidence_state()
        step = tools.save_workmap_step(state, StepProposal.model_validate(proposal(answer.id)))
        copied = tools.get_workmap(state)
        copied.steps[0].status = "confirmed"
        step.status = "confirmed"
        self.assertEqual(state.steps[0].status, "proposed")
        with self.assertRaises(ValueError):
            tools.save_workmap_step(state, StepProposal.model_validate(proposal(answer.id)))

    def test_finish_requires_knowledge_and_both_expert_approvals(self):
        """Block early finish, unconfirmed steps, missing teach-back and pending answers."""
        state, _, answer = evidence_state()
        with self.assertRaises(ValueError):
            tools.finish_session(state)
        tools.start_debrief(state)
        with self.assertRaises(ValueError):
            tools.finish_session(state)
        step = tools.save_workmap_step(state, StepProposal.model_validate(proposal(answer.id)))
        with self.assertRaises(ValueError):
            tools.confirm_teach_back(state, "Vincent")
        tools.confirm_step(state, step.id, "Vincent")
        with self.assertRaises(ValueError):
            tools.finish_session(state)
        tools.confirm_teach_back(state, "Vincent")
        tools.ask_expert(state, QuestionRequest(event_id="event-1", text="When would you stop and ask?", question_type="guardrail"))
        self.assertIsNone(state.teach_back_confirmed_by)
        with self.assertRaises(ValueError):
            tools.finish_session(state)


if __name__ == "__main__":
    unittest.main()
