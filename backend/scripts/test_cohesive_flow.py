"""Full HTTP -> real PostgREST -> Postgres teaching flow using synthetic data.

Start scripts/compose.audit.yml and apply schema/migrations first. No team DB writes.
Use --live-model to exercise the configured builder on the same synthetic fixture.
Gemini requires a Google Free tier project with billing disabled.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from uuid import uuid4
from unittest.mock import AsyncMock,patch

import httpx
from postgrest import AsyncPostgrestClient
from app import store, api_llm
from app.main import app
from app.routes import capture,tutor
from app.agent import interview


def token():
    def part(value): return base64.urlsafe_b64encode(json.dumps(value,separators=(',',':')).encode()).rstrip(b'=')
    signed=part({'alg':'HS256','typ':'JWT'})+b'.'+part({'role':'service_role','exp':int(time.time())+3600})
    signature=hmac.new(b'local-audit-only-secret-at-least-32-characters',signed,hashlib.sha256).digest()
    return (signed+b'.'+base64.urlsafe_b64encode(signature).rstrip(b'=')).decode()


async def main():
    os.environ['APP_AUTH_MODE']='local'
    db=AsyncPostgrestClient('http://127.0.0.1:55433',headers={'Authorization':'Bearer '+token()})
    store._db=db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
        async def request(method,path,body=None,expected=200):
            result=await client.request(method,path,json=body)
            if result.status_code!=expected:
                # Only our synthetic input participates in this isolated test.
                raise AssertionError(f'{method} {path}: expected {expected}, got {result.status_code}; {result.json().get("detail","")}')
            return result.json()
        fields=[{'name':'amount','type':'number','description':'Expense amount'},
                {'name':'receipt','type':'boolean','description':'Receipt present'},
                {'name':'status','type':'string','description':'Review status'}]
        workflow=await request('POST','/api/workflows',{'name':'Synthetic audit '+uuid4().hex,'app':'Fixture'})
        wid=workflow['id']
        await request('PATCH',f'/api/workflows/{wid}',{'fields':fields})
        session=await request('POST','/api/capture/sessions',{'session_id':str(uuid4()),'expert_name':'Synthetic expert','workflow_id':wid})
        sid=session['id']; root=f'/api/capture/sessions/{sid}'
        body={'client_id':'screen-1','t':1,'actions':['changed status to on_hold'],
              'events':[{'event_id':'event-1','event_type':'field_change','target':'status','old_value':'pending','new_value':'on_hold',
                         'timestamp_ms':round(session['started_at']*1000)+1000,'page':'/synthetic','description':'Set expense to on_hold'}],
              'page':'Synthetic expense. Amount: 120. Receipt: unchecked. Status: on_hold.'}
        await request('POST',root+'/observations',body)
        await request('POST',root+'/observations',body)
        actual=await request('GET',root)
        assert len(actual['events'])==1
        assert actual['events'][0]['observed_action']['old_value']=='pending'
        assert actual['events'][0]['observed_action']['new_value']=='on_hold'
        turn={'client_id':'explanation','t':2,'role':'expert',
              'text':'I hold expenses above 75 without a receipt. Set status to on_hold before saving.'}
        await request('POST',root+'/transcript',turn); await request('POST',root+'/transcript',turn)
        actual=await request('GET',root); assert len(actual['transcript'])==1
        frame={'screen_summary':'Expense held','changed':True,'event_kind':'field_change',
               'event_description':'Expense held','record_fields_json':'{"amount":120,"receipt":false,"status":"on_hold"}',
               'is_decision_point':True,'ask_why':'Why did you put it on hold?'}
        with patch.object(capture,'structured',AsyncMock(return_value=frame)):
            await request('POST',root+'/frames',body)
            await request('POST',root+'/frames',body)
        assert len((await request('GET',root))['events'])==1
        await request('POST',root+'/debrief',{})
        before_resume=await request('GET',root)
        resumed=await request('POST',root+'/resume',{})
        assert resumed['phase']=='capture'
        assert resumed['transcript']==before_resume['transcript'] and resumed['events']==before_resume['events']
        await request('POST',root+'/debrief',{})
        draft={'title':'Hold expenses missing receipts','supersedes':None,
               'trigger':[{'field':'amount','op':'gt','values':['75']},{'field':'receipt','op':'is_false','values':[]}],
               'action':{'kind':'hold','detail':'Set status to on_hold'},'expert_explanation':turn['text'],
               'guardrail':{'description':'Keep on hold until a receipt arrives','must':[{'field':'status','op':'eq','values':['on_hold']}]},
               'evidence':[{'t':2,'quote':turn['text'],'segment_id':actual['transcript'][0]['segment_id'],'event_id':actual['events'][0]['event_id']}]}
        model={'summary':'Synthetic expense review','fields':fields,'skills':[draft]}
        live='--live-model' in sys.argv
        if live:
            wm=await request('POST',root+'/workmap',{'client_id':'build-1','expected_revision':0})
        else:
            with patch.object(capture,'structured',AsyncMock(return_value=model)) as builder:
                wm=await request('POST',root+'/workmap',{'client_id':'build-1','expected_revision':0})
            assert '"old_value": "pending"' in builder.call_args.kwargs['content']
        cached=await request('POST',root+'/workmap',{'client_id':'build-1','expected_revision':0})
        assert cached['skills'][0]['id']==wm['skills'][0]['id']
        for sk in wm['skills']:
            await request('PATCH','/api/skills/'+sk['id'],{'status':'approved','expected_version':sk['version']})
        wm=await request('GET','/api/workmaps/'+sid)
        await request('PATCH',f'/api/workflows/{wid}',{'fields':[]},expected=422)
        teach=await request('GET',root+'/teach-back'); assert teach['text'] and teach['revision']==wm['revision']
        await request('POST',root+'/teach-back/presented',{'expected_revision':wm['revision']})
        await request('POST',root+'/teach-back',{'expected_revision':wm['revision']})
        assert (await request('GET','/api/workmaps/'+sid))['teach_back_confirmed']
        await request('POST',root+'/resume',{},expected=409)
        case=await request('POST',f'/api/workflows/{wid}/cases',{'label':'Synthetic case','data':{'amount':120,'receipt':False,'status':'pending'}})
        started=await request('POST','/api/tutor/sessions',{'learner_name':'Synthetic learner','workflow_id':wid,'case_id':case['id']})
        ts=started['session']['id']; tr=f'/api/tutor/sessions/{ts}'
        assert 'skill_snapshot' not in started['session']
        pinned=await store.get_tutor_session(ts); assert pinned.skill_snapshot
        first=started['decision_points'][0]['skill_id']
        with patch.object(tutor,'structured',AsyncMock(return_value={'correct':True,'feedback':'Hold for the receipt.'})):
            await request('POST',tr+'/predict',{'skill_id':first,'prediction':'Put it on hold because the receipt is missing.'})
        recovered=await request('GET',tr)
        assert recovered['grades'][first]['correct'] and recovered['fields']==pinned.workflow_snapshot.model_dump()['fields']
        blocked=await request('POST',tr+'/check',{'record':{'amount':10,'receipt':True,'status':'approved'}}); assert not blocked['ok']
        unknown=await request('POST',tr+'/check',{'record':{'amount':120,'receipt':False}}); assert not unknown['ok'] and unknown['unknown_fields']
        passing=await request('POST',tr+'/check',{'record':{'amount':120,'receipt':False,'status':'on_hold'}}); assert passing['ok']
        report=await request('GET',tr+'/report'); assert report['skills'][0]['status']=='practicing'
        # Build an edited successor and preserve publication until its explicit approval.
        edit=json.loads(json.dumps(wm)); edit['skills'][0]['title']='Revised hold instruction'
        edited=await request('PUT','/api/workmaps/'+sid,edit)
        new=next(s for s in edited['skills'] if s['status']=='draft' and s['supersedes']==first)
        assert (await store.get_skill(first)).status=='approved'
        await request('PATCH','/api/skills/'+new['id'],{'status':'approved','expected_version':new['version']})
        assert (await store.get_skill(first)).status=='rejected'
        restored=await store.get_tutor_session(ts); assert restored.skill_snapshot[0].id==pinned.skill_snapshot[0].id
        assert (await request('GET',tr+'/report'))['skills'][0]['version']==pinned.skill_snapshot[0].version
        # Exercise actual checkpoint hydration and question delivery evidence.
        bound=await request('POST','/api/capture/sessions',{'expert_name':'Synthetic expert','workflow_id':wid})
        br=f'/api/capture/sessions/{bound["id"]}'
        question={'action':'ask_question','reason':'Need guardrail reasoning','question':{'event_id':'bounded-event','text':'Why hold this expense?','question_type':'reason'},'step':None,'step_id':None}
        with patch.object(interview,'structured',AsyncMock(return_value=question)):
            event=await request('POST',br+'/apprentice/events',{'client_id':'bounded-batch','events':[{'event_id':'bounded-event','event_type':'click','target':'Hold'}],'context':{'expert_paused':True}})
        qid=event['event']['question_id']
        await request('POST',br+'/transcript',{'client_id':'spoken-question','t':1,'role':'agent','text':'Why hold this expense?','question_id':qid})
        await request('POST',br+'/transcript',{'client_id':'spoken-answer','t':2,'role':'expert','text':'The receipt is missing.','question_id':qid})
        state=await request('GET',br+'/apprentice'); assert state['answers'][0]['text']=='The receipt is missing.'
        assert state['answers'][0]['segment_id']
        checkpoint=(await db.table('apprentice_states').select('state').eq('session_id',bound['id']).execute()).data[0]['state']
        assert checkpoint['events']=={} and checkpoint['answers']==[]
        print('PASS: real HTTP capture -> durable evidence -> draft -> approval -> teach-back -> pinned tutor -> guardrails -> mastery; bounded checkpoint evidence also verified.')
        print('Model boundary: '+('live '+':'.join(api_llm.configured_model())+' builder' if live else 'synthetic provider fixtures'))
    await db.aclose()


if __name__=='__main__':
    asyncio.run(main())
