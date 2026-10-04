"""Regression checks for the working conversational capture and optional Qwen mode."""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from app.routes import capture
from app.agent import interview
from app.agent.schemas import WorkMapState, QuestionRequest, CaptureContext
from app.agent import tools
from app.events.normalizer import normalize_browser_event
from app.models import CaptureSession, Workflow, ScreenEvent, TranscriptTurn


class ConversationalCaptureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = CaptureSession(id="session", workflow_id="workflow", expert_name="Expert", started_at=10)
        self.workflow = Workflow(id="workflow", name="EMR review", app="OpenEMR", description="Review modifier decisions")
        self.query = MagicMock()
        self.query.select.return_value = self.query
        self.query.eq.return_value = self.query
        self.query.execute = AsyncMock(return_value=SimpleNamespace(data=[]))
        for item in [patch.object(capture.store,"_t",return_value=self.query),
                     patch.object(capture.store,"ingest_capture",AsyncMock(return_value={"analyzed":False,"result":None})),
                     patch.object(capture.store,"complete_batch",AsyncMock())]:
            item.start(); self.addCleanup(item.stop)

    async def test_default_frame_keeps_full_emr_snapshot_and_existing_question_contract(self):
        self.session.events=[ScreenEvent(t=0.5,kind="field_change",description="Prior receipt action retained",
            observed_action={"target":"status","old_value":"pending","new_value":"on_hold"})]
        result = {"screen_summary":"Modifier screen", "changed":True, "event_kind":"modifier_change",
            "event_description":"Changed modifier to 25", "record_fields_json":'{"modifier":"25"}',
            "is_decision_point":True, "ask_why":"Why is modifier 25 needed?"}
        with patch.object(capture,"_get",AsyncMock(return_value=self.session)), \
             patch.object(capture,"_workflow",AsyncMock(return_value=self.workflow)), \
             patch.object(capture,"structured",AsyncMock(return_value=result)) as reasoning, \
             patch.object(capture.store,"complete_batch",AsyncMock()) as record, \
             patch.object(capture.interview,"analyze",AsyncMock()) as qwen:
            event = await capture.analyze_frame("session",capture.FrameBody(t=1,
                actions=['changed "Modifier" to "25"'],page="Visible EMR fields: CPT 99214; Modifier 25"))
        qwen.assert_not_awaited()
        content=reasoning.call_args.kwargs["content"][0]["text"]
        self.assertIn("CPT 99214; Modifier 25",content)
        self.assertIn("EMR review",content)
        self.assertIn("Prior receipt action retained",content)
        self.assertIn('"old_value": "pending"',content)
        self.assertEqual(event.ask_why,result["ask_why"])
        self.assertEqual(event.record_fields,{"modifier":"25"})
        record.assert_awaited_once()

    async def test_workmap_builder_still_creates_existing_supabase_skills(self):
        self.session.events=[ScreenEvent(t=1,kind="change",description="Modifier changed")]
        self.session.transcript=[TranscriptTurn(t=2,role="expert",text="Check the separate service")]
        result={"summary":"Expert workflow", "fields":[], "skills":[{
            "title":"Review modifier", "trigger":[], "action":{"kind":"other","detail":"Check modifier"},
            "expert_explanation":"Check the separate service", "guardrail":{"description":"Check first","must":[]},
            "evidence":[]}]}
        with patch.object(capture,"_get",AsyncMock(return_value=self.session)), \
             patch.object(capture,"_workflow",AsyncMock(return_value=self.workflow)), \
             patch.object(capture,"structured",AsyncMock(return_value=result)), \
             patch.object(capture.store,"get_workmap",AsyncMock(return_value=None)), \
             patch.object(capture.store,"create_workmap",AsyncMock(return_value="stored-workmap")) as create:
            self.assertEqual(await capture.build_workmap("session",capture.BuildBody()),"stored-workmap")
        skills=create.call_args.args[2]
        self.assertEqual(skills[0].status,"draft")
        self.assertEqual(skills[0].title,"Review modifier")

    async def test_recorded_speech_without_website_events_reports_the_missing_source(self):
        from fastapi import HTTPException
        self.session.transcript=[TranscriptTurn(t=1,role="expert",text="Synthetic reasoning")]
        with patch.object(capture,"_get",AsyncMock(return_value=self.session)), \
             patch.object(capture,"_workflow",AsyncMock(return_value=self.workflow)), \
             patch.object(capture,"structured",AsyncMock()) as model:
            with self.assertRaises(HTTPException) as failure:
                await capture._build_workmap("session",capture.BuildBody())
        self.assertEqual(failure.exception.status_code,400)
        self.assertIn("No website actions",failure.exception.detail)
        model.assert_not_awaited()

    async def test_built_or_finished_capture_cannot_reopen_evidence(self):
        from fastapi import HTTPException
        for phase,revision in [("debrief",1),("finished",0)]:
            self.session.phase=phase; self.session.workmap_revision=revision
            with patch.object(capture,"_get",AsyncMock(return_value=self.session)):
                with self.assertRaises(HTTPException) as failure:
                    await capture.resume_capture("session")
                self.assertEqual(failure.exception.status_code,409)

    async def test_plain_voice_transcript_requires_no_new_migration(self):
        with patch.object(capture,"_get",AsyncMock(return_value=self.session)), \
             patch.object(capture.store,"add_turn",AsyncMock()) as save, \
             patch.object(capture.interview,"link_turn",AsyncMock()) as link:
            await capture.add_turn("session",capture.LinkedTurn(t=1,role="expert",text="My reasoning"))
        save.assert_awaited_once()
        link.assert_not_awaited()

    async def test_optional_qwen_gets_workflow_and_page_context(self):
        decision={"action":"ask_question","reason":"Need explanation","step":None,"step_id":None,
            "question":{"event_id":"event","text":"Why modifier 25?","question_type":"reason"}}
        with patch.object(interview.persistence,"load",AsyncMock(return_value=(WorkMapState(expert_name="Expert"),0))), \
             patch.object(interview.persistence,"save",AsyncMock(return_value=1)), \
             patch.object(interview,"structured",AsyncMock(return_value=decision)) as llm:
            event=await interview.analyze(self.session,self.workflow,capture.FrameBody(t=1,reasoning_provider="qwen",
                page="EMR full page snapshot",events=[{"event_id":"event","event_type":"field_change",
                "target":"Modifier","new_value":"25"}],context=CaptureContext(expert_paused=True)))
        import json
        content=json.loads(llm.call_args.kwargs["content"])
        self.assertEqual(content["page_snapshot"],"EMR full page snapshot")
        self.assertEqual(content["workflow"]["app"],"OpenEMR")
        self.assertIsNotNone(event.question_id)

    async def test_question_delivery_is_verified_before_answer_can_be_linked(self):
        state=WorkMapState(expert_name="Expert")
        event=normalize_browser_event({"event_id":"event","event_type":"click","target":"Review"})
        state.events[event.event_id]=event
        question=tools.ask_expert(state,QuestionRequest(event_id="event",text="Why review?",question_type="reason"))
        async def save(session,updated,revision,**kwargs):
            self.assertEqual(updated.steps,[])
            return 1
        with patch.object(interview.persistence,"load",AsyncMock(return_value=(state,0))), \
             patch.object(interview.persistence,"save",side_effect=save):
            with self.assertRaises(ValueError):
                await interview.link_turn(self.session,capture.LinkedTurn(t=1,role="expert",text="Missing information",question_id=question.id))
            with self.assertRaises(ValueError):
                await interview.link_turn(self.session,capture.LinkedTurn(t=1,role="agent",text="A different question?",question_id=question.id))
            await interview.link_turn(self.session,capture.LinkedTurn(t=1,role="agent",text="Why review?",question_id=question.id))
            await interview.link_turn(self.session,capture.LinkedTurn(t=2,role="expert",text="Missing information",question_id=question.id))
        self.assertEqual(state.answers[0].question_id,question.id)
        self.assertEqual(state.answers[0].text,"Missing information")

    def test_ui_keeps_conversation_and_build_callback(self):
        from pathlib import Path
        source=(Path(__file__).resolve().parents[2]/"extension/src/sidepanel/Capture.tsx").read_text()
        self.assertIn("useConversation(",source)
        self.assertIn("snapshotPage(boundTab.current",source)
        self.assertIn("onBuilt(wm)",source)
        self.assertIn("Build Work Map",source)
        self.assertIn("useState(false)",source)
