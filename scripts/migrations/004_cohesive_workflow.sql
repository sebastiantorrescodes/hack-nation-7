-- Apply after 003. Additive migration: existing approved skills and sessions are retained.
begin;
alter table sessions add column if not exists owner_principal uuid;
alter table sessions add column if not exists capture_phase text not null default 'capture';
alter table sessions add column if not exists workmap_revision integer not null default 0;
alter table sessions add column if not exists teach_back_revision integer;
alter table sessions add column if not exists teach_back_presented_revision integer;
alter table sessions add column if not exists skill_snapshot jsonb;
alter table sessions add column if not exists workflow_snapshot jsonb;
alter table skills add column if not exists supersedes uuid references skills(id);
alter table skills add column if not exists build_revision integer not null default 0;
alter table events add column if not exists client_id text;
alter table transcript_segments add column if not exists client_id text;
alter table transcript_segments add column if not exists question_id text;
alter table transcript_segments add column if not exists answer_id text;
alter table transcript_segments add column if not exists linked_event_client_id text;
alter table attempts add column if not exists ordinal bigint generated always as identity;
create unique index if not exists events_client_identity on events(session_id,client_id) where client_id is not null;
create unique index if not exists transcript_client_identity on transcript_segments(session_id,client_id) where client_id is not null;
create unique index if not exists one_skill_successor on skills(supersedes) where status='approved' and supersedes is not null;
create table if not exists capture_batches (
 session_id uuid references sessions(id) on delete cascade,
 client_id text not null,
 payload jsonb not null,
 analyzed boolean not null default false,
 result jsonb,
 primary key(session_id,client_id)
);
create table if not exists workmap_builds (
 session_id uuid references sessions(id) on delete cascade,
 client_id text not null,
 revision integer not null,
 payload jsonb not null,
 primary key(session_id,client_id)
);
-- Membership is provisioned by the team administrator, never granted by the client.
create table if not exists skill_reviews (
 id bigint generated always as identity primary key, skill_id uuid references skills(id),
 actor_id uuid references users(id), status text not null, version integer not null,
 created_at timestamptz not null default clock_timestamp()
);
alter table skill_reviews enable row level security;
create table if not exists app_members (
 auth_id uuid primary key,
 user_id uuid not null references users(id),
 role text not null check(role in ('expert','trainee','admin'))
);
-- Public clients access the domain through the authenticated backend, not service-role tables.
alter table users enable row level security;
alter table workflows enable row level security;
alter table cases enable row level security;
alter table sessions enable row level security;
alter table events enable row level security;
alter table transcript_segments enable row level security;
alter table skills enable row level security;
alter table skill_evidence enable row level security;
alter table attempts enable row level security;
alter table interventions enable row level security;
alter table capture_batches enable row level security;
alter table workmap_builds enable row level security;
alter table app_members enable row level security;

create or replace function ingest_capture(p_session uuid,p_client text,p_payload jsonb)
returns jsonb language plpgsql security invoker set search_path=public as $$
declare s sessions; old capture_batches; item jsonb; cid text; prior jsonb;
begin
 select * into strict s from sessions where id=p_session and kind='capture' for update;
 select * into old from capture_batches where session_id=p_session and client_id=p_client;
 if found then
  if old.payload <> p_payload then raise exception 'capture_identity_conflict'; end if;
  return jsonb_build_object('analyzed',old.analyzed,'result',old.result);
 end if;
 if s.capture_phase <> 'capture' then raise exception 'capture_closed'; end if;
 insert into capture_batches(session_id,client_id,payload) values(p_session,p_client,p_payload);
 for item in select value from jsonb_array_elements(coalesce(p_payload->'events','[]')) loop
  cid := item->>'event_id';
  if cid is null or cid='' then raise exception 'event_identity_required'; end if;
  select payload->'normalized_event' into prior from events where session_id=p_session and client_id=cid;
  if found then
   if prior<>item then raise exception 'event_identity_conflict'; end if;
  else
   insert into events(session_id,client_id,type,t_offset_ms,page,field,new_value,old_value,payload)
   values(p_session,cid,item->>'event_type',greatest(0,coalesce((item->>'timestamp_ms')::bigint,
     (extract(epoch from s.started_at)*1000)::bigint)-(extract(epoch from s.started_at)*1000)::bigint),
     item->>'page',item->>'target',item->>'new_value',item->>'old_value',
     jsonb_build_object('description',coalesce(item->>'description',item->>'event_type'),'normalized_event',item));
  end if;
 end loop;
 return jsonb_build_object('analyzed',false,'result',null);
end $$;

create or replace function ingest_transcript(p_session uuid,p_client text,p_turn jsonb)
returns bigint language plpgsql security invoker set search_path=public as $$
declare old transcript_segments; sid bigint; s sessions;
begin
 select * into strict s from sessions where id=p_session and kind='capture' for update;
 select * into old from transcript_segments where session_id=p_session and client_id=p_client;
 if found then
  if old.text<>p_turn->>'text' or old.speaker<>p_turn->>'role' or old.t_start_ms<>round((p_turn->>'t')::numeric*1000) then
   raise exception 'transcript_identity_conflict';
  end if;
  return old.id;
 end if;
 if s.capture_phase='finished' then raise exception 'capture_closed'; end if;
 insert into transcript_segments(session_id,client_id,speaker,t_start_ms,text)
 values(p_session,p_client,p_turn->>'role',round((p_turn->>'t')::numeric*1000),p_turn->>'text') returning id into sid;
 return sid;
end $$;

create or replace function complete_capture_batch(p_session uuid,p_client text,p_result jsonb,p_summary text)
returns void language plpgsql security invoker set search_path=public as $$
begin
 perform 1 from sessions where id=p_session for update;
 update capture_batches set analyzed=true,result=p_result where session_id=p_session and client_id=p_client;
 if not found then raise exception 'capture_batch_missing'; end if;
 update sessions set screen_summary=p_summary where id=p_session;
end $$;

-- Validate every reference inside the transaction as well as in the application.
create or replace function insert_draft_skill(p_session uuid,p_workflow uuid,p_skill jsonb,p_revision integer)
returns uuid language plpgsql security invoker set search_path=public as $$
declare sid uuid; e jsonb; seg transcript_segments; ev events; parent uuid; v integer:=1;
begin
 if nullif(trim(p_skill->>'title'),'') is null or nullif(trim(p_skill->'action'->>'detail'),'') is null
  or nullif(trim(p_skill->>'expert_explanation'),'') is null
  or jsonb_array_length(p_skill->'trigger')=0 or jsonb_array_length(p_skill->'guardrail'->'must')=0
  or jsonb_array_length(p_skill->'evidence')=0 then raise exception 'incomplete_skill'; end if;
 parent:=nullif(p_skill->>'supersedes','')::uuid;
 if parent is not null then
  select version+1 into strict v from skills where id=parent and source_session=p_session;
 end if;
 insert into skills(workflow_id,source_session,name,trigger,action,action_kind,reason_quote,guardrail,guardrail_msg,
   status,version,supersedes,build_revision)
 values(p_workflow,p_session,p_skill->>'title',jsonb_build_object('all',p_skill->'trigger'),p_skill->'action'->>'detail',
   p_skill->'action'->>'kind',p_skill->>'expert_explanation',jsonb_build_object('all',p_skill->'guardrail'->'must'),
   p_skill->'guardrail'->>'description','draft',v,parent,p_revision) returning id into sid;
 for e in select value from jsonb_array_elements(p_skill->'evidence') loop
  select * into strict seg from transcript_segments where id=(e->>'segment_id')::bigint and session_id=p_session
   and speaker='expert' and not coalesce(off_record,false);
  select * into strict ev from events where id=(e->>'event_id')::bigint and session_id=p_session;
  if nullif(trim(e->>'quote'),'') is null or strpos(lower(seg.text),lower(e->>'quote'))=0 then raise exception 'invalid_expert_quote'; end if;
  insert into skill_evidence(skill_id,event_id,segment_id,quote) values(sid,ev.id,seg.id,e->>'quote');
 end loop;
 return sid;
end $$;

-- Field edits must preserve the meaning of every draft and published condition.
create or replace function preserve_skill_fields(p_workflow uuid,p_fields jsonb)
returns void language plpgsql security invoker set search_path=public as $$
declare w workflows; dependency record; old_type text; new_type text;
begin
 select * into strict w from workflows where id=p_workflow for update;
 for dependency in
  select distinct c->>'field' as name from skills k,
   lateral jsonb_array_elements(coalesce(k.trigger->'all','[]') || coalesce(k.guardrail->'all','[]')) c
   where k.workflow_id=p_workflow and k.status in ('draft','approved')
 loop
  select f->>'type' into old_type from jsonb_array_elements(w.fields) f where f->>'name'=dependency.name;
  select f->>'type' into new_type from jsonb_array_elements(p_fields) f where f->>'name'=dependency.name;
  if new_type is null or (old_type is not null and new_type<>old_type) then
   raise exception 'A draft or published skill requires this field and its existing type';
  end if;
 end loop;
end $$;

create or replace function update_workflow_atomic(p_workflow uuid,p_changes jsonb)
returns void language plpgsql security invoker set search_path=public as $$
begin
 perform 1 from workflows where id=p_workflow for update;
 if p_changes ? 'fields' then perform preserve_skill_fields(p_workflow,p_changes->'fields'); end if;
 update workflows set
  name=case when p_changes ? 'name' then p_changes->>'name' else name end,
  app=case when p_changes ? 'app' then p_changes->>'app' else app end,
  description=case when p_changes ? 'description' then p_changes->>'description' else description end,
  fields=case when p_changes ? 'fields' then p_changes->'fields' else fields end
  where id=p_workflow;
end $$;
revoke all on function preserve_skill_fields(uuid,jsonb),update_workflow_atomic(uuid,jsonb) from public,anon,authenticated;
grant execute on function preserve_skill_fields(uuid,jsonb),update_workflow_atomic(uuid,jsonb) to service_role;

create or replace function build_workmap_atomic(p_session uuid,p_client text,p_expected integer,p_payload jsonb)
returns integer language plpgsql security invoker set search_path=public as $$
declare s sessions; old workmap_builds; sk jsonb; rev integer;
begin
 select * into strict s from sessions where id=p_session and kind='capture' for update;
 select * into old from workmap_builds where session_id=p_session and client_id=p_client;
 if found then
  if old.payload<>p_payload then raise exception 'build_identity_conflict'; end if;
  return old.revision;
 end if;
 if s.workmap_revision<>p_expected then raise exception 'workmap_revision_conflict'; end if;
 if s.capture_phase='finished' and p_payload->>'mode'<>'edit' then raise exception 'capture_closed'; end if;
 if jsonb_array_length(p_payload->'skills')=0 then raise exception 'no_evidence_backed_skills'; end if;
 rev:=s.workmap_revision+1;
 if p_payload ? 'fields' then perform preserve_skill_fields(s.workflow_id,p_payload->'fields'); end if;
 -- Replace drafts only. The published map survives until the expert approves its successor.
 update skills set status='rejected',approved_by=null where source_session=p_session and status='draft';
 for sk in select value from jsonb_array_elements(p_payload->'skills') loop
  if p_payload->>'mode'='edit' and nullif(sk->>'id','') is not null then
   perform 1 from skills where id=(sk->>'id')::uuid and source_session=p_session;
   if not found then raise exception 'skill_session_mismatch'; end if;
  end if;
  perform insert_draft_skill(p_session,s.workflow_id,sk,rev);
 end loop;
 if p_payload ? 'fields' then update workflows set fields=p_payload->'fields' where id=s.workflow_id; end if;
 update sessions set summary=p_payload->>'summary',status='processing',workmap_revision=rev,teach_back_revision=null,teach_back_presented_revision=null,
  capture_phase=case when p_payload->>'mode'='edit' then 'debrief' else capture_phase end where id=p_session;
 insert into workmap_builds values(p_session,p_client,rev,p_payload);
 return rev;
end $$;

create or replace function review_skill_atomic(p_skill uuid,p_expected integer,p_status text,p_actor uuid)
returns void language plpgsql security invoker set search_path=public as $$
declare sk skills; sibling skills; sess sessions;
begin
 -- Lock the session first for a consistent lock order with build/edit.
 select * into strict sess from sessions where id=(select source_session from skills where id=p_skill) for update;
 select * into strict sk from skills where id=p_skill for update;
 if sk.version<>p_expected then raise exception 'skill_version_conflict'; end if;
 if sk.status=p_status then return; end if;
 if p_status not in ('draft','approved','rejected') then raise exception 'invalid_status'; end if;
 if sk.status='rejected' and p_status='approved' then raise exception 'rejected_version'; end if;
 if p_status='approved' then
  if p_actor is null or not exists(select 1 from users where id=p_actor and role in ('expert','admin')) then raise exception 'expert_required'; end if;
  if sk.status<>'approved' then
   if sk.build_revision<>sess.workmap_revision then raise exception 'stale_draft'; end if;
   if not exists(select 1 from skill_evidence e join transcript_segments t on t.id=e.segment_id
      join events v on v.id=e.event_id where e.skill_id=p_skill and t.session_id=sk.source_session
      and v.session_id=sk.source_session and t.speaker='expert' and not coalesce(t.off_record,false)
      and nullif(trim(e.quote),'') is not null and strpos(lower(t.text),lower(e.quote))>0) then raise exception 'expert_evidence_required'; end if;
   if jsonb_array_length(sk.guardrail->'all')=0 then raise exception 'guardrail_required'; end if;
  end if;
  if sk.supersedes is not null then
   update skills set status='rejected' where id=sk.supersedes and status='approved';
  end if;
 end if;
 insert into skill_reviews(skill_id,actor_id,status,version) values(p_skill,p_actor,p_status,sk.version);
 update skills set status=p_status,approved_by=case when p_status='approved' then p_actor else null end where id=p_skill;
 update sessions set teach_back_revision=null,teach_back_presented_revision=null,capture_phase=case when capture_phase='finished' then 'debrief' else capture_phase end where id=sk.source_session;
end $$;

-- Snapshot legacy training sessions now; every new training session stores a complete immutable map.
update sessions s set skill_snapshot=coalesce((select jsonb_agg(jsonb_build_object(
 'id',k.id,'title',k.name,'trigger',k.trigger->'all','action',jsonb_build_object('kind',coalesce(k.action_kind,'other'),'detail',k.action),
 'expert_explanation',coalesce(k.reason_quote,''),'guardrail',jsonb_build_object('description',coalesce(k.guardrail_msg,''),'must',coalesce(k.guardrail->'all','[]')),
 'status','approved','version',k.version,'expert_name',coalesce(u.name,'')))
 from skills k left join sessions c on c.id=k.source_session left join users u on u.id=c.user_id
 where k.workflow_id=s.workflow_id and k.id=any(s.matched_skill_ids)),'[]'),
 workflow_snapshot=(select jsonb_build_object('id',w.id,'name',w.name,'app',w.app,'description',coalesce(w.description,''),'fields',w.fields) from workflows w where w.id=s.workflow_id)
 where s.kind='training' and s.skill_snapshot is null;

-- Mastery requires the first prediction AND first save check to succeed in the same session.
-- Later retries cannot manufacture mastery. Column names/types remain compatible with the old view.
create or replace view mastery with (security_invoker=true) as
with ordered as (select attempts.*,row_number() over(partition by session_id,skill_id order by created_at,ordinal) as position from attempts), firsts as (
 select distinct on(session_id,skill_id,(prediction is null)) trainee_id,session_id,skill_id,
 prediction is null as save_check,outcome,position from ordered order by session_id,skill_id,(prediction is null),position
), session_result as (
 select trainee_id,session_id,skill_id,bool_or(not save_check and outcome='correct') as predicted,
 bool_or(save_check and outcome='correct') as saved,bool_or(outcome<>'correct') as helped,
 min(position) filter(where not save_check) < min(position) filter(where save_check) as predicted_first
 from firsts group by trainee_id,session_id,skill_id
)
select trainee_id,skill_id,count(*) as times_seen,
 count(*) filter(where predicted and saved and not helped and predicted_first) as correct,
 count(*) filter(where helped) as mistakes,
 case when count(*) filter(where predicted and saved and not helped and predicted_first)>=1 then 'mastered'
      when bool_or(predicted or saved or helped) then 'learning' else 'needs_practice' end as level
from session_result group by trainee_id,skill_id;
alter view published_work_map set (security_invoker=true);
-- No domain RPC is callable with a browser/anonymous Supabase key.
revoke all on function ingest_capture(uuid,text,jsonb),ingest_transcript(uuid,text,jsonb),complete_capture_batch(uuid,text,jsonb,text),
 insert_draft_skill(uuid,uuid,jsonb,integer),build_workmap_atomic(uuid,text,integer,jsonb),review_skill_atomic(uuid,integer,text,uuid) from public,anon,authenticated;
grant execute on function ingest_capture(uuid,text,jsonb),ingest_transcript(uuid,text,jsonb),complete_capture_batch(uuid,text,jsonb,text),
 insert_draft_skill(uuid,uuid,jsonb,integer),build_workmap_atomic(uuid,text,integer,jsonb),review_skill_atomic(uuid,integer,text,uuid) to service_role;
notify pgrst,'reload schema';
-- Explicit expert confirmation of the exact reviewed revision, independent of LLM claims.
create or replace function confirm_capture_teach_back(p_session uuid,p_expected integer)
returns void language plpgsql security invoker set search_path=public as $$
declare s sessions;
begin
 select * into strict s from sessions where id=p_session and kind='capture' for update;
 if s.workmap_revision<>p_expected or p_expected=0 then raise exception 'workmap_revision_conflict'; end if;
 if s.capture_phase not in ('debrief','finished') then raise exception 'debrief_required'; end if;
 if s.teach_back_presented_revision is distinct from p_expected then raise exception 'present_teach_back_first'; end if;
 if exists(select 1 from skills where source_session=p_session and status='draft')
  or not exists(select 1 from skills where source_session=p_session and status='approved' and build_revision=p_expected)
 then raise exception 'review_all_skills_first'; end if;
 if exists(select 1 from apprentice_states a where a.session_id=p_session
  and exists(select 1 from jsonb_array_elements(coalesce(a.state->'questions','[]')) q
   where not exists(select 1 from transcript_segments t where t.session_id=p_session and t.question_id=q->>'id' and t.answer_id is not null)))
 then raise exception 'pending_expert_answer'; end if;
 update sessions set teach_back_revision=p_expected,capture_phase='finished',status='done',ended_at=now() where id=p_session;
end $$;
revoke all on function confirm_capture_teach_back(uuid,integer) from public,anon,authenticated;
grant execute on function confirm_capture_teach_back(uuid,integer) to service_role;
notify pgrst,'reload schema';
-- Evidence-backed expert answers are stored once, on their transcript segments.
create unique index if not exists one_answer_identity on transcript_segments(session_id,answer_id) where answer_id is not null;
create or replace function save_capture_checkpoint(p_session uuid,p_state jsonb,p_revision integer,p_links jsonb default '[]')
returns integer language plpgsql security invoker set search_path=public as $$
declare current_revision integer; a jsonb;
begin
 perform 1 from sessions where id=p_session for update;
 insert into apprentice_states(session_id,state,revision) values(p_session,'{}',0) on conflict do nothing;
 select revision into current_revision from apprentice_states where session_id=p_session for update;
 if current_revision<>p_revision then raise exception 'apprentice_revision_conflict'; end if;
 for a in select value from jsonb_array_elements(p_links) loop
  if not exists(select 1 from jsonb_array_elements(p_state->'questions') q
    where q->>'id'=a->>'question_id' and q->>'event_id'=a->>'event_id')
   or not exists(select 1 from events where session_id=p_session and client_id=a->>'event_id')
  then raise exception 'answer_question_event_mismatch'; end if;
  if not exists(select 1 from transcript_segments spoken join transcript_segments answered on answered.id=(a->>'segment_id')::bigint
    where spoken.id=(p_state->'delivery_segments'->>(a->>'question_id'))::bigint and spoken.session_id=p_session
    and spoken.speaker='agent' and not coalesce(spoken.off_record,false) and answered.t_start_ms>=spoken.t_start_ms)
   then raise exception 'answer_before_question_delivery'; end if;
  update transcript_segments set question_id=a->>'question_id',answer_id=a->>'id',linked_event_client_id=a->>'event_id'
   where id=(a->>'segment_id')::bigint and session_id=p_session and speaker='expert' and not coalesce(off_record,false)
   and text=a->>'text' and (answer_id is null or answer_id=a->>'id');
  if not found then raise exception 'answer_segment_mismatch'; end if;
 end loop;
 update apprentice_states set state=p_state,revision=current_revision+1 where session_id=p_session;
 return current_revision+1;
end $$;
revoke all on function save_capture_checkpoint(uuid,jsonb,integer,jsonb) from public,anon,authenticated;
grant execute on function save_capture_checkpoint(uuid,jsonb,integer,jsonb) to service_role;

notify pgrst,'reload schema';
create or replace function start_tutor_atomic(p_actor uuid,p_owner uuid,p_workflow uuid,p_record jsonb,
 p_matched uuid[],p_skills jsonb,p_workflow_snapshot jsonb)
returns uuid language plpgsql security invoker set search_path=public as $$
declare sk jsonb; k skills; w workflows; sid uuid;
begin
 select * into strict w from workflows where id=p_workflow for share;
 if w.fields<>p_workflow_snapshot->'fields' then raise exception 'workflow_version_conflict'; end if;
 for sk in select value from jsonb_array_elements(p_skills) loop
  select * into strict k from skills where id=(sk->>'id')::uuid and workflow_id=p_workflow for share;
  if k.status<>'approved' or k.version<>(sk->>'version')::integer then raise exception 'skill_version_conflict'; end if;
 end loop;
 if exists(select 1 from unnest(p_matched) m where not exists(select 1 from jsonb_array_elements(p_skills) x(value) where x.value->>'id'=m::text))
 then raise exception 'matched_skill_mismatch'; end if;
 insert into sessions(kind,user_id,owner_principal,workflow_id,record,matched_skill_ids,skill_snapshot,workflow_snapshot)
 values('training',p_actor,p_owner,p_workflow,p_record,p_matched,p_skills,p_workflow_snapshot) returning id into sid;
 return sid;
end $$;

create or replace function record_tutor_attempt(p_session uuid,p_kind text,p_skill uuid,p_prediction text,
 p_correct boolean,p_feedback text,p_blocked jsonb default '{}')
returns void language plpgsql security invoker set search_path=public as $$
declare s sessions; sid uuid; failed boolean; message text;
begin
 select * into strict s from sessions where id=p_session and kind='training' for update;
 if p_kind='prediction' then
  if p_skill is null or p_skill<>all(s.matched_skill_ids) then raise exception 'matched_skill_mismatch'; end if;
  if exists(select 1 from attempts where session_id=p_session and skill_id=p_skill and prediction is not null)
   then raise exception 'prediction_identity_conflict'; end if;
  insert into attempts(session_id,skill_id,trainee_id,prediction,outcome,tutor_message)
   values(p_session,p_skill,s.user_id,p_prediction,case when p_correct then 'correct' else 'hinted' end,p_feedback);
  insert into interventions(session_id,skill_id,kind,message) values(p_session,p_skill,case when p_correct then 'praise' else 'explain' end,p_feedback);
 elsif p_kind='save_check' then
  foreach sid in array s.matched_skill_ids loop
   failed:=p_blocked ? sid::text;
   message:=p_blocked->>sid::text;
   insert into attempts(session_id,skill_id,trainee_id,outcome,blocked_save,tutor_message)
    values(p_session,sid,s.user_id,case when failed then 'caught' else 'correct' end,failed,message);
   if failed then insert into interventions(session_id,skill_id,kind,message) values(p_session,sid,'block',message); end if;
  end loop;
  update sessions set status=case when p_blocked='{}' then 'done' else 'live' end,
   ended_at=case when p_blocked='{}' then now() else null end where id=p_session;
 else raise exception 'invalid_attempt_kind'; end if;
end $$;
revoke all on function start_tutor_atomic(uuid,uuid,uuid,jsonb,uuid[],jsonb,jsonb),record_tutor_attempt(uuid,text,uuid,text,boolean,text,jsonb) from public,anon,authenticated;
grant execute on function start_tutor_atomic(uuid,uuid,uuid,jsonb,uuid[],jsonb,jsonb),record_tutor_attempt(uuid,text,uuid,text,boolean,text,jsonb) to service_role;

grant all on capture_batches,workmap_builds,app_members,apprentice_states,skill_reviews to service_role;
notify pgrst,'reload schema';

create or replace function present_capture_teach_back(p_session uuid,p_expected integer)
returns void language plpgsql security invoker set search_path=public as $$
declare s sessions;
begin
 select * into strict s from sessions where id=p_session and kind='capture' for update;
 if s.workmap_revision<>p_expected then raise exception 'workmap_revision_conflict'; end if;
 if s.capture_phase<>'debrief' then raise exception 'debrief_required'; end if;
 if exists(select 1 from skills where source_session=p_session and status='draft') then raise exception 'review_all_skills_first'; end if;
 update sessions set teach_back_presented_revision=p_expected where id=p_session;
end $$;
revoke all on function present_capture_teach_back(uuid,integer) from public,anon,authenticated;
grant execute on function present_capture_teach_back(uuid,integer) to service_role;
notify pgrst,'reload schema';
commit;
