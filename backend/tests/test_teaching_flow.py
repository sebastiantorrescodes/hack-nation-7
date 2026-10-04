"""Exercise the standard teaching flow without database writes or paid model calls."""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import store
from app.models import Attempt, PracticeCase, Skill, TutorSession, Workflow
from app.routes import tutor, workmaps


class TeachingFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.workflow = Workflow(id="workflow", name="Expense review", app="Test form")
        self.skill = Skill(
            id="skill", title="Hold reports missing a receipt",
            trigger=[{"field": "amount", "op": "gt", "values": ["75"]},
                     {"field": "receipt", "op": "is_false"}],
            action={"kind": "hold", "detail": "Set status to on_hold"},
            expert_explanation="I hold this because a receipt is required above 75.",
            guardrail={"description": "Keep this report on hold until evidence arrives",
                       "must": [{"field": "status", "op": "eq", "values": ["on_hold"]}]},
            status="approved", expert_name="Test expert",
        )
        self.record = {"amount": 120, "receipt": False, "status": "pending"}
        self.session = TutorSession(
            id="session", learner_name="Test learner", workflow_id="workflow",
            case_id="case", original_record=self.record, matched_skill_ids=["skill"],
        )

    async def test_no_approved_skills_prevents_training_session_creation(self):
        with patch.object(tutor, "_workflow", AsyncMock(return_value=self.workflow)), \
             patch.object(store, "published_skills", AsyncMock(return_value=[])), \
             patch.object(store, "create_tutor_session", AsyncMock()) as create:
            with self.assertRaises(HTTPException) as error:
                await tutor.start(tutor.StartBody(learner_name="Test learner", workflow_id="workflow", case_id="case"))
        self.assertEqual(error.exception.status_code, 400)
        create.assert_not_awaited()

    async def test_case_from_another_workflow_is_rejected(self):
        case = PracticeCase(id="case", workflow_id="different", label="Test case", data=self.record)
        with patch.object(tutor, "_workflow", AsyncMock(return_value=self.workflow)), \
             patch.object(store, "published_skills", AsyncMock(return_value=[self.skill])), \
             patch.object(store, "get_case", AsyncMock(return_value=case)), \
             patch.object(store, "create_tutor_session", AsyncMock()) as create:
            with self.assertRaises(HTTPException) as error:
                await tutor.start(tutor.StartBody(learner_name="Test learner", workflow_id="workflow", case_id="case"))
        self.assertEqual(error.exception.status_code, 404)
        create.assert_not_awaited()

    async def test_start_shows_decision_points_without_revealing_expert_answer(self):
        case = PracticeCase(id="case", workflow_id="workflow", label="Test case", data=self.record)
        with patch.object(tutor, "_workflow", AsyncMock(return_value=self.workflow)), \
             patch.object(store, "published_skills", AsyncMock(return_value=[self.skill])), \
             patch.object(store, "get_case", AsyncMock(return_value=case)), \
             patch.object(store, "create_tutor_session", AsyncMock(return_value=self.session)) as create:
            result = await tutor.start(tutor.StartBody(learner_name="Test learner", workflow_id="workflow", case_id="case"))
        self.assertEqual(create.call_args.args[-1], ["skill"])
        point = result["decision_points"][0]
        self.assertNotIn("expert_explanation", point)
        self.assertNotIn("action", point)

    async def test_editing_trigger_fields_cannot_evade_original_record_guardrail(self):
        edited = {"amount": 10, "receipt": True, "status": "approved"}
        with patch.object(tutor, "_session", AsyncMock(return_value=(self.session, self.workflow, [self.skill]))), \
             patch.object(store, "record_save_check", AsyncMock()) as save:
            result = await tutor.check_before_save("session", tutor.CheckBody(record=edited))
        self.assertFalse(result["ok"])
        self.assertEqual(result["violations"][0]["skill_id"], "skill")
        self.assertEqual(result["violations"][0]["expert_explanation"], self.skill.expert_explanation)
        save.assert_awaited_once_with(self.session, {"skill": self.skill.guardrail.description})

    async def test_corrected_record_passes_and_records_success(self):
        with patch.object(tutor, "_session", AsyncMock(return_value=(self.session, self.workflow, [self.skill]))), \
             patch.object(store, "record_save_check", AsyncMock()) as save:
            result = await tutor.check_before_save("session", tutor.CheckBody(record={**self.record, "status": "on_hold"}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["violations"], [])
        save.assert_awaited_once_with(self.session, {})

    async def test_prediction_is_graded_against_expert_reasoning_and_saved(self):
        grade = {"correct": True, "feedback": "Keep it on hold until a receipt arrives."}
        with patch.object(tutor, "_session", AsyncMock(return_value=(self.session, self.workflow, [self.skill]))), \
             patch.object(tutor, "structured", AsyncMock(return_value=grade)) as model, \
             patch.object(store, "record_prediction", AsyncMock()) as save:
            result = await tutor.predict("session", tutor.PredictBody(skill_id="skill", prediction="Put it on hold"))
        self.assertIn(self.skill.expert_explanation, model.call_args.kwargs["content"])
        self.assertEqual(result["expert_name"], "Test expert")
        save.assert_awaited_once_with(self.session, "skill", "Put it on hold", True, grade["feedback"])

    async def test_report_uses_attempt_history(self):
        self.session.attempts = [Attempt(skill_id="skill", kind="prediction", correct=False, detail="Hint needed")]
        result = {"headline": "Keep practicing", "skills": [{"skill_id": "skill", "status": "practicing", "note": "Hint needed"}], "practice_next": ["Check receipts"]}
        with patch.object(tutor, "_session", AsyncMock(return_value=(self.session, self.workflow, [self.skill]))), \
             patch.object(tutor, "structured", AsyncMock(return_value=result)) as model:
            report = await tutor.report("session")
        model.assert_not_awaited()
        self.assertEqual(report["skills"][0]["status"], "practicing")
        self.assertEqual(report["skills"][0]["title"], self.skill.title)

    async def test_workflows_without_approved_skills_are_not_published(self):
        drafts = self.workflow.model_copy(update={"draft_skills": 2})
        approved = self.workflow.model_copy(update={"id": "published", "approved_skills": 1})
        with patch.object(store, "list_workflows", AsyncMock(return_value=[drafts, approved])):
            published = await store.list_published_workflows(None)
        self.assertEqual([w.id for w in published], ["published"])

    async def test_review_routes_explicit_approval_to_existing_skill_store(self):
        with patch.object(store, "set_skill_status", AsyncMock(return_value=self.skill)) as save:
            result = await workmaps.review_skill("skill", workmaps.StatusBody(status="approved",expected_version=1))
        self.assertEqual(result.status, "approved")
        save.assert_awaited_once_with("skill", "approved", 1)


if __name__ == "__main__":
    unittest.main()
