"""API handoffs use fake provider boundaries; transaction behavior is tested in Postgres."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
from app.agent.schemas import WorkMapState
from app.api_llm import ReasoningAPIError
from app.events.normalizer import normalize_browser_event
from app.models import CaptureSession, Workflow
from app.routes import apprentice, capture
from app.main import app

EVENT={'event_id':'event-1','event_type':'field_change','target':'Status','new_value':'Needs review','timestamp_ms':11000,'page':'/review'}
QUESTION={'action':'ask_question','reason':'Need expert reasoning','step':None,'step_id':None,
          'question':{'event_id':'event-1','text':'Why needs review?','question_type':'reason'}}

class CaptureIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state=WorkMapState(expert_name='Expert'); self.revision=0; self.events={}; self.turns=[]; self.batches={}
        self.session=CaptureSession(id='session',workflow_id='workflow',expert_name='Expert',started_at=10)
        async def load(*args):
            state=self.state.model_copy(deep=True); state.events={k:v.model_copy(deep=True) for k,v in self.events.items()}
            return state,self.revision
        async def save(session,state,revision,**kwargs):
            self.assertEqual(revision,self.revision); self.state=state.model_copy(deep=True); self.revision+=1; return self.revision
        async def ingest(sid,cid,payload):
            for raw in payload.get('events',[]):
                event=normalize_browser_event(raw)
                if event.event_id in self.events and self.events[event.event_id]!=event: raise ValueError('conflict')
                self.events[event.event_id]=event
            return self.batches.get(cid,{'analyzed':False,'result':None})
        async def complete(sid,cid,event,summary):
            self.batches[cid]={'analyzed':True,'result':event.model_dump() if event else None}
        async def turn(sid,body):
            self.turns.append(body.model_dump()); return len(self.turns)
        self.query=MagicMock()
        for name in ['select','eq','order','limit']: getattr(self.query,name).return_value=self.query
        self.query.execute=AsyncMock(side_effect=lambda: SimpleNamespace(data=[{'id':i+1,'speaker':t['role'],'text':t['text']} for i,t in enumerate(self.turns) if t['role']=='agent'][-1:]))
        patches=[patch.object(capture.store,'get_capture_session',AsyncMock(return_value=self.session)),
            patch.object(capture.store,'get_workflow',AsyncMock(return_value=Workflow(id='workflow',name='Fixture',app='EMR'))),
            patch.object(capture.store,'ingest_capture',side_effect=ingest),patch.object(capture.store,'complete_batch',side_effect=complete),
            patch.object(capture.store,'add_turn',side_effect=turn),patch.object(capture.store,'_t',return_value=self.query),
            patch.object(apprentice.persistence,'load',side_effect=load),patch.object(apprentice.persistence,'save',side_effect=save)]
        for item in patches: item.start(); self.addCleanup(item.stop)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://test')
        self.addAsyncCleanup(self.client.aclose); self.root='/api/capture/sessions/session/apprentice'

    async def event(self,paused=True,events=None):
        return await self.client.post(self.root+'/events',json={'events':[EVENT] if events is None else events,
            'context':{'expert_paused':paused,'expert_busy':not paused}})

    async def test_quiet_capture_persists_events_without_model_call(self):
        with patch('app.agent.interview.structured',AsyncMock()) as llm: response=await self.event(False)
        self.assertEqual(response.status_code,200); self.assertIsNone(response.json()['event']); llm.assert_not_awaited()
        self.assertEqual(len(self.events),1)

    async def test_pause_question_audio_answer_and_restart_state(self):
        with patch('app.agent.interview.structured',AsyncMock(return_value=QUESTION)): response=await self.event()
        self.assertEqual(response.status_code,200); question=response.json()['state']['questions'][0]
        self.assertEqual(self.turns,[]) # A proposed question is not a spoken transcript.
        with patch.object(apprentice.ElevenLabsSpeech,'speak',AsyncMock(return_value=b'mp3')):
            audio=await self.client.get(f'{self.root}/questions/{question["id"]}/audio')
        self.assertEqual(audio.content,b'mp3'); self.assertEqual(self.state.delivered_question_ids,[])
        speech=await self.client.post('/api/capture/sessions/session/transcript',json={'client_id':'spoken','t':1,'role':'agent','text':'Why needs review?','question_id':question['id']})
        self.assertTrue(speech.json()['question_linked'])
        with patch.object(apprentice.ElevenLabsSpeech,'transcribe',AsyncMock(return_value='Missing information.')):
            result=await self.client.post(f'{self.root}/questions/{question["id"]}/answer',content=b'recording',headers={'Content-Type':'audio/webm'})
        self.assertEqual(result.status_code,200)
        recovered=(await self.client.get(self.root)).json()
        self.assertEqual(recovered['answers'][0]['text'],'Missing information.'); self.assertEqual(recovered['steps'],[])
        repeat=await self.client.post(f'{self.root}/questions/{question["id"]}/answer',content=b'recording',headers={'Content-Type':'audio/webm'})
        self.assertEqual(repeat.status_code,409)

    async def test_duplicate_event_is_not_inserted_twice(self):
        await self.event(False); await self.event(False); self.assertEqual(len(self.events),1)

    async def test_model_failure_does_not_lose_observed_facts(self):
        with patch('app.agent.interview.structured',AsyncMock(side_effect=ReasoningAPIError('Unavailable'))): response=await self.event()
        self.assertEqual(response.status_code,502); self.assertIn('event-1',self.events); self.assertEqual(self.state.questions,[])

    async def test_forged_browser_source_and_unrelated_question_rejected(self):
        result=await self.event(events=[{**EVENT,'source':'vision'}]); self.assertEqual(result.status_code,422)
        result=await self.client.get(self.root+'/questions/unknown/audio'); self.assertEqual(result.status_code,409)

    async def test_missing_session_does_not_load_checkpoint(self):
        with patch.object(apprentice.store,'get_capture_session',AsyncMock(return_value=None)): result=await self.client.get(self.root)
        self.assertEqual(result.status_code,404)
