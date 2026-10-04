-- Synthetic integration test. Run in the disposable audit database, never the team database.
begin;
do $$
declare expert uuid; learner uuid; wf uuid; cap uuid; training uuid; replay uuid;
 seg bigint; agentseg bigint; offseg bigint; evt bigint; draft uuid; successor uuid;
 skill jsonb; payload jsonb; revision integer; saved jsonb; snapshot jsonb;
begin
 insert into users(name,role) values('Synthetic audit expert','expert') returning id into expert;
 insert into users(name,role) values('Synthetic audit learner','trainee') returning id into learner;
 insert into workflows(name,app,fields) values('Synthetic expense audit','Fixture',
 '[{"name":"amount","type":"number"},{"name":"receipt","type":"boolean"},{"name":"status","type":"string"}]') returning id into wf;
 insert into sessions(workflow_id,user_id,kind) values(wf,expert,'capture') returning id into cap;
 payload:='{"events":[{"event_id":"evt","event_type":"field_change","target":"status","new_value":"on_hold","description":"Put expense on hold"}],"page":"Synthetic screen"}';
 saved:=ingest_capture(cap,'batch',payload);
 assert (saved->>'analyzed')::boolean=false, 'new batch not pending';
 perform ingest_capture(cap,'batch',payload);
 assert (select count(*) from events where session_id=cap)=1, 'duplicate event inserted';
 begin
  perform ingest_capture(cap,'batch',jsonb_set(payload,'{page}','"different"'));
  raise exception 'test_expected_identity_rejection';
 exception when others then assert sqlerrm='capture_identity_conflict','identity conflict did not reject'; end;
 seg:=ingest_transcript(cap,'turn','{"t":1,"role":"expert","text":"I hold this because expenses above 75 need a receipt."}');
 assert ingest_transcript(cap,'turn','{"t":1,"role":"expert","text":"I hold this because expenses above 75 need a receipt."}')=seg,'transcript replay changed';
 agentseg:=ingest_transcript(cap,'agent','{"t":0,"role":"agent","text":"I hold this because expenses above 75 need a receipt."}');
 offseg:=ingest_transcript(cap,'private','{"t":2,"role":"expert","text":"Off-record quote"}');
 update transcript_segments set off_record=true where id=offseg;
 select id into evt from events where session_id=cap;
 perform complete_capture_batch(cap,'batch','{"kind":"field_change"}','Status review');
 assert (ingest_capture(cap,'batch',payload)->>'analyzed')::boolean, 'analysis replay not cached';
 skill:=jsonb_build_object('id','','title','Hold missing receipts','trigger',
 '[{"field":"amount","op":"gt","values":["75"]},{"field":"receipt","op":"is_false","values":[]}]'::jsonb,
 'action','{"kind":"hold","detail":"Set status to on_hold"}'::jsonb,'expert_explanation','Expenses above 75 need a receipt.',
 'guardrail','{"description":"Keep on hold","must":[{"field":"status","op":"eq","values":["on_hold"]}]}'::jsonb,
 'evidence',jsonb_build_array(jsonb_build_object('t',1,'quote','expenses above 75 need a receipt.','segment_id',seg,'event_id',evt)));
 payload:=jsonb_build_object('summary','Synthetic reviewed map','skills',jsonb_build_array(skill),'mode','build');
 revision:=build_workmap_atomic(cap,'build-1',0,payload);
 assert revision=1, 'build revision incorrect';
 assert build_workmap_atomic(cap,'build-1',0,payload)=1, 'build replay not idempotent';
 select id into draft from skills where source_session=cap and status='draft';
 begin
  perform build_workmap_atomic(cap,'bad-build',1,jsonb_set(payload,'{skills,0,evidence,0,segment_id}',to_jsonb(agentseg)));
  raise exception 'test_expected_agent_evidence_rejection';
 exception when others then assert sqlerrm<>'test_expected_agent_evidence_rejection','agent speech accepted'; end;
 assert (select status from skills where id=draft)='draft', 'failed build destroyed draft';
 assert (select workmap_revision from sessions where id=cap)=1, 'failed build changed revision';
 begin
  perform build_workmap_atomic(cap,'bad-quote',1,jsonb_set(payload,'{skills,0,evidence,0,quote}','"Invented quote"'));
  raise exception 'test_expected_quote_rejection';
 exception when others then assert sqlerrm='invalid_expert_quote','invented quote accepted'; end;
 begin
  perform review_skill_atomic(draft,2,'approved',expert);
  raise exception 'test_expected_version_rejection';
 exception when others then assert sqlerrm='skill_version_conflict','stale approval accepted'; end;
 perform review_skill_atomic(draft,1,'approved',expert);
 assert (select approved_by from skills where id=draft)=expert,'wrong approval identity';
 -- Tutor snapshots an approved version before a replacement is built.
 snapshot:=jsonb_build_array(jsonb_build_object('id',draft,'version',1,'title','Hold missing receipts'));
 training:=start_tutor_atomic(learner,null,wf,'{"amount":120,"receipt":false,"status":"pending"}',array[draft],snapshot,
  jsonb_build_object('fields',(select fields from workflows where id=wf)));
 -- A new draft is a successor, and leaves the old published version intact.
 skill:=skill||jsonb_build_object('supersedes',draft);
 payload:=jsonb_build_object('summary','Revised map','skills',jsonb_build_array(skill),'mode','edit');
 perform build_workmap_atomic(cap,'build-2',1,payload);
 assert (select status from skills where id=draft)='approved','rebuild unpublished approved skill';
 select id into successor from skills where supersedes=draft and status='draft';
 assert (select version from skills where id=successor)=2,'edited skill not versioned';
 begin
  perform confirm_capture_teach_back(cap,2);
  raise exception 'test_expected_unreviewed_rejection';
 exception when others then assert sqlerrm in ('debrief_required','review_all_skills_first','present_teach_back_first'),'unreviewed map finished'; end;
 perform review_skill_atomic(successor,2,'approved',expert);
 assert (select status from skills where id=draft)='rejected','successor did not replace publication';
 assert (select skill_snapshot->0->>'id' from sessions where id=training)=draft::text,'tutor snapshot changed';
 -- Repeated save checks alone cannot produce mastery.
 perform record_tutor_attempt(training,'save_check',null,null,null,null,'{}');
 perform record_tutor_attempt(training,'save_check',null,null,null,null,'{}');
 assert (select level from mastery where trainee_id=learner and skill_id=draft)<>'mastered','save spam created mastery';
 perform record_tutor_attempt(training,'prediction',draft,'Put it on hold',false,'Explanation');
 assert (select level from mastery where trainee_id=learner and skill_id=draft)<>'mastered','hinted prediction created mastery';
 begin
  perform record_tutor_attempt(training,'prediction',draft,'Retry after hint',true,'Correct');
  raise exception 'test_expected_prediction_rejection';
 exception when others then assert sqlerrm='prediction_identity_conflict','second prediction accepted'; end;
 -- Mastery of the new version requires a first correct prediction and first correct check.
 snapshot:=jsonb_build_array(jsonb_build_object('id',successor,'version',2));
 replay:=start_tutor_atomic(learner,null,wf,'{"amount":120,"receipt":false,"status":"pending"}',array[successor],snapshot,
  jsonb_build_object('fields',(select fields from workflows where id=wf)));
 perform record_tutor_attempt(replay,'prediction',successor,'Hold for receipt',true,'Correct');
 perform record_tutor_attempt(replay,'save_check',null,null,null,null,'{}');
 assert (select level from mastery where trainee_id=learner and skill_id=successor)='mastered','valid mastery not recognized';
 update sessions set capture_phase='debrief' where id=cap;
 perform present_capture_teach_back(cap,2);
 perform confirm_capture_teach_back(cap,2);
 assert (select capture_phase from sessions where id=cap)='finished','teach-back did not finish';
 -- Auth roles cannot invoke service-only write RPCs.
 assert not has_function_privilege('anon','ingest_capture(uuid,text,jsonb)','execute'),'anonymous capture RPC exposed';
 assert not has_function_privilege('authenticated','review_skill_atomic(uuid,integer,text,uuid)','execute'),'browser publication RPC exposed';
 raise notice 'PASS: capture replay, rollback, exact evidence, approval versions, preserved publication, tutor pinning, mastery, teach-back and RPC access';
end $$;
rollback;
