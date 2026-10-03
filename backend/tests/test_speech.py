"""Verify speech boundaries and the answer-to-agent path without live providers."""

import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.speech import ElevenLabsSpeech, SpeechError


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_speak_sends_exact_question_and_returns_audio(self):
        def handler(request):
            self.assertIn('/text-to-speech/', request.url.path)
            self.assertEqual(json.loads(request.content)['text'], 'Why did you change the status?')
            return httpx.Response(200, content=b'mp3', headers={'content-type':'audio/mpeg'})
        speech = ElevenLabsSpeech(transport=httpx.MockTransport(handler))
        speech.key = 'fake'
        self.assertEqual(await speech.speak('Why did you change the status?'), b'mp3')

    async def test_transcription_preserves_exact_expert_text(self):
        def handler(request):
            self.assertIn(b'scribe_v2', request.content)
            self.assertIn(b'recorded bytes', request.content)
            return httpx.Response(200, json={'text':'  I need a second review.  '})
        speech = ElevenLabsSpeech(transport=httpx.MockTransport(handler))
        speech.key = 'fake'
        self.assertEqual(await speech.transcribe(b'recorded bytes','audio/webm'), '  I need a second review.  ')

    async def test_errors_and_empty_results(self):
        for response in [httpx.Response(401, text='secret'), httpx.Response(200, json={'text':''})]:
            speech = ElevenLabsSpeech(transport=httpx.MockTransport(lambda r:response))
            speech.key = 'fake'
            with self.assertRaises(SpeechError) as error:
                await speech.transcribe(b'audio', 'audio/webm')
            self.assertNotIn('secret', str(error.exception))

    async def test_input_limits(self):
        speech = ElevenLabsSpeech()
        with self.assertRaises(ValueError):
            await speech.transcribe(b'', 'audio/webm')
        with self.assertRaises(ValueError):
            await speech.speak('x'*301)


class DemoTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_answer_is_evidence_and_model_proposal_is_unconfirmed(self):
        from app import voice_demo as demo
        from app.agent.schemas import CaptureContext, WorkMapState
        from app.agent.apprentice import process_event
        from app.events.normalizer import normalize_browser_event
        from fastapi import Request

        state=WorkMapState(expert_name='Expert')
        event=normalize_browser_event({'event_id':'event','event_type':'click','target':'Review'})
        decision={'action':'ask_question','reason':'Need explanation','question':{
            'event_id':'event','text':'Why review?','question_type':'reason'},'step':None,'step_id':None}
        await process_event(event,state,CaptureContext(expert_paused=True),reason=AsyncMock(return_value=decision))
        demo.sessions['test']=state
        demo.locks['test']=__import__('asyncio').Lock()
        question=state.questions[0]
        async def receive():return {'type':'http.request','body':b'audio','more_body':False}
        request=Request({'type':'http','headers':[(b'content-type',b'audio/webm')]},receive)
        async def proposal(**kwargs):
            evidence=state.answers[0]
            return {'action':'save_step','reason':'Expert answered','question':None,'step_id':None,'step':{
                'title':'Request review','action':'Request a second review','reason':evidence.text,
                'guardrails':None,'event_ids':['event'],'answer_ids':[evidence.id]}}
        try:
            with patch.object(demo.ElevenLabsSpeech,'transcribe',AsyncMock(return_value='I need a second review.')), \
                 patch('app.api_llm.structured',side_effect=proposal):
                result=await demo.answer('test',question.id,request)
            self.assertEqual(result['answer'].question_id,question.id)
            self.assertEqual(result['answer'].event_id,'event')
            self.assertEqual(state.steps[0].status,'proposed')
            self.assertIsNone(state.steps[0].confirmed_by)
            with self.assertRaises(Exception) as error:
                await demo.answer('test',question.id,request)
            self.assertEqual(error.exception.status_code,409)
        finally:
            demo.sessions.pop('test',None);demo.locks.pop('test',None)
