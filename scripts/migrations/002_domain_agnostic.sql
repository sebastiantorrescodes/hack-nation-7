-- Makes the schema domain-agnostic: each workflow defines its own record fields, practice cases live in
-- the database, and healthcare-specific names are generalized. Run once in the Supabase SQL editor. Safe to re-run.

-- What a workflow's records look like: [{"name": "amount", "type": "number", "description": "..."}].
-- Skill triggers and guardrails are conditions over these fields. Claude proposes them from the first
-- capture session; the expert can edit them.
alter table workflows add column if not exists description text;
alter table workflows add column if not exists fields jsonb not null default '[]';
alter table workflows alter column app set default '';

-- events.encounter_id -> record_id, sessions.claim -> record
do $$ begin
  if exists (select 1 from information_schema.columns where table_name = 'events' and column_name = 'encounter_id') then
    alter table events rename column encounter_id to record_id;
  end if;
  if exists (select 1 from information_schema.columns where table_name = 'sessions' and column_name = 'claim') then
    alter table sessions rename column claim to record;
  end if;
end $$;

-- Training sessions now practice a whole workflow, not one capture session
alter table sessions drop column if exists capture_session_id;

-- Practice cases: records as they arrive, before anyone has worked them, for trainees to practice on
create table if not exists cases (
  id          uuid primary key default gen_random_uuid(),
  workflow_id uuid references workflows(id) on delete cascade,
  label       text not null,
  data        jsonb not null,
  created_at  timestamptz default now()
);
create index if not exists cases_workflow_id_idx on cases (workflow_id);
