"""Safety and version boundaries that previously fragmented the teaching flow."""
import unittest
from unittest.mock import AsyncMock,patch
import httpx
from app import rules,store,access
from app.knowledge import KnowledgeError,draft_payload,resolve_evidence
from app.models import Attempt,EvidenceRef,RecordField,Skill,TutorSession,Workflow
from app.routes import tutor,capture
from app.main import app


def skill():
    return Skill(id='skill',version=2,title='Hold for receipt',trigger=[{'field':'receipt','op':'is_false'}],
      action={'kind':'hold','detail':'Hold'},expert_explanation='Missing receipt',
      guardrail={'description':'Hold','must':[{'field':'status','op':'eq','values':['on_hold']}]},
      evidence=[{'t':1,'quote':'Missing receipt','segment_id':1,'event_id':2}],status='approved')

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.e=EvidenceRef(t=1,quote='Missing receipt',segment_id=1,event_id=2)
        self.segments=[{'id':1,'t_start_ms':1000,'text':'Missing receipt','speaker':'expert','off_record':False}]
    def test_exact_quote_resolves_recorded_references(self):
        resolved=resolve_evidence(self.e,self.segments,[{'id':2}]); self.assertEqual(resolved.segment_id,1)
    def test_invented_quote_does_not_fall_back_to_nearest_segment(self):
        with self.assertRaises(KnowledgeError): resolve_evidence(self.e.model_copy(update={'quote':'Invented'}),self.segments,[{'id':2}])
    def test_agent_and_off_record_quotes_are_excluded(self):
        for change in [{'speaker':'agent'},{'off_record':True}]:
            with self.assertRaises(KnowledgeError): resolve_evidence(self.e,[{**self.segments[0],**change}],[{'id':2}])
    def test_cross_session_event_is_rejected(self):
        with self.assertRaises(KnowledgeError): resolve_evidence(self.e,self.segments,[{'id':3}])
    def test_empty_guardrail_cannot_become_teachable(self):
        sk=skill(); sk.guardrail.must=[]
        with self.assertRaises(KnowledgeError): draft_payload([sk],[RecordField(name='receipt',type='boolean')],self.segments,[{'id':2}])
    def test_ambiguous_legacy_timestamp_does_not_guess(self):
        with self.assertRaises(KnowledgeError): resolve_evidence(self.e.model_copy(update={'segment_id':None}),self.segments+[dict(self.segments[0],id=3)],[{'id':2}])

class RuleTests(unittest.TestCase):
    def test_unknown_boolean_is_not_false(self):
        cond=skill().trigger[0]
        for record in [{},{'receipt':None},{'receipt':'false'}]:
            self.assertIsNone(rules.evaluate(cond,record)); self.assertFalse(rules.check(cond,record))
            with self.assertRaises(rules.UnknownRecord): rules.triggered_skills([skill()],record)
    def test_malformed_numbers_block_rule_verification(self):
        sk=skill(); sk.trigger=[type(sk.trigger[0])(field='amount',op='gt',values=['75'])]
        sk=Skill.model_validate(sk.model_dump())
        for value in [None,'NaN','Infinity',True]:
            with self.assertRaises(rules.UnknownRecord): rules.triggered_skills([sk],{'amount':value})
    def test_definitely_unrelated_rule_does_not_require_unknown_fields(self):
        sk=skill(); sk.trigger.append(type(sk.trigger[0])(field='status',op='eq',values=['closed']))
        self.assertEqual(rules.triggered_skills([sk],{'status':'open'}),[])
    def test_missing_guardrail_value_blocks_even_if_trigger_matches(self):
        with self.assertRaises(rules.UnknownRecord): rules.guardrail_violations([skill()],{'receipt':False},{})
    def test_save_check_spam_never_creates_mastery(self):
        attempts=[Attempt(skill_id='skill',kind='save_check',correct=True,detail='')]*5
        self.assertEqual(rules.mastery_status(attempts,'skill'),'practicing')
    def test_first_attempts_determine_mastery(self):
        attempts=[Attempt(skill_id='skill',kind='prediction',correct=False,detail=''),Attempt(skill_id='skill',kind='save_check',correct=True,detail='')]
        attempts.append(Attempt(skill_id='skill',kind='prediction',correct=True,detail=''))
        self.assertEqual(rules.mastery_status(attempts,'skill'),'practicing')
        attempts[0]=attempts[-1]; self.assertEqual(rules.mastery_status(attempts,'skill'),'mastered')
        self.assertEqual(rules.mastery_status(list(reversed(attempts[:2])),'skill'),'practicing')

class HandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_tutor_uses_snapshot_after_publication_changes(self):
        wf=Workflow(id='wf',name='Pinned workflow',app='Fixture'); sk=skill()
        ts=TutorSession(id='session',learner_name='Learner',workflow_id='wf',case_id='case',original_record={'receipt':False},matched_skill_ids=['skill'],skill_snapshot=[sk],workflow_snapshot=wf)
        with patch.object(store,'get_tutor_session',AsyncMock(return_value=ts)),patch.object(store,'published_skills',AsyncMock(return_value=[])) as published:
            _,actual,skills=await tutor._session('session')
        published.assert_not_awaited(); self.assertEqual(skills[0].version,2); self.assertEqual(actual.name,'Pinned workflow')
    async def test_unknown_save_result_is_blocked_without_marking_mastery_failure(self):
        sk=skill(); wf=Workflow(id='wf',name='Fixture',app='Test')
        ts=TutorSession(id='s',learner_name='Learner',workflow_id='wf',case_id='case',original_record={'receipt':False},matched_skill_ids=['skill'])
        with patch.object(tutor,'_session',AsyncMock(return_value=(ts,wf,[sk]))),patch.object(store,'record_save_check',AsyncMock()) as record:
            result=await tutor.check_before_save('s',tutor.CheckBody(record={}))
        self.assertFalse(result['ok']); self.assertEqual(result['unknown_fields'],['status']); record.assert_not_awaited()
    async def test_capture_is_committed_before_model_failure(self):
        from app.models import CaptureSession
        from app.api_llm import ReasoningAPIError
        calls=[]
        async def ingest(*args): calls.append('committed'); return {'analyzed':False,'result':None}
        async def model(**kwargs): calls.append('model'); raise ReasoningAPIError('unavailable')
        with patch.object(capture,'_get',AsyncMock(return_value=CaptureSession(id='s',workflow_id='wf',expert_name='E',started_at=0))),patch.object(capture,'_workflow',AsyncMock(return_value=Workflow(id='wf',name='W',app='A'))),patch.object(store,'ingest_capture',side_effect=ingest),patch.object(capture,'structured',side_effect=model):
            with self.assertRaises(ReasoningAPIError): await capture.analyze_frame('s',capture.FrameBody(t=1,page='full synthetic screen'))
        self.assertEqual(calls,['committed','model'])

class AccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_mode_refuses_remote_and_forwarded_requests(self):
        for address,headers in [('203.0.113.2',{}),('127.0.0.1',{'x-forwarded-for':'203.0.113.2'})]:
            transport=httpx.ASGITransport(app=app,client=(address,123))
            async with httpx.AsyncClient(transport=transport,base_url='http://localhost') as client:
                response=await client.get('/api/workflows',headers=headers)
            self.assertEqual(response.status_code,401)
    async def test_authenticated_mode_requires_token(self):
        with patch.dict('os.environ',{'APP_AUTH_MODE':'supabase'}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
                response=await client.get('/api/workflows')
        self.assertEqual(response.status_code,401)
    async def test_trainee_cannot_publish_or_capture(self):
        with patch.object(access,'request_principal',AsyncMock(return_value=access.Principal(auth_id='auth',user_id='user',role='trainee',local=False))):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
                for path in ['/api/skills/skill','/api/capture/sessions']:
                    response=await client.post(path,json={})
                    self.assertEqual(response.status_code,403)
                response=await client.delete('/api/cases/case')
                self.assertEqual(response.status_code,403)
    def test_session_ownership_is_checked(self):
        from fastapi import HTTPException
        key=access.caller.set(access.Principal(auth_id='mine',role='expert',local=False))
        try:
            with self.assertRaises(HTTPException): access.authorize_owner({'owner_principal':'someone-else'})
            access.authorize_owner({'owner_principal':'mine'})
        finally: access.caller.reset(key)
