-- AI Apprentice: Supabase / Postgres schema
-- Flow: expert capture session -> raw events/transcript/media -> Work Map (skills)
--       -> expert approves -> published Work Map -> trainee sessions -> attempts -> mastery

create extension if not exists "pgcrypto";

-- ───────────── People ─────────────
create table users (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  role        text not null check (role in ('expert', 'trainee', 'admin')),
  created_at  timestamptz default now()
);

-- A workflow being taught, e.g. "Outpatient E/M billing in OpenEMR"
create table workflows (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  app         text not null default 'OpenEMR',
  created_at  timestamptz default now()
);

-- ───────────── 1. CAPTURE (expert) ─────────────
create table sessions (
  id            uuid primary key default gen_random_uuid(),
  workflow_id   uuid references workflows(id) on delete cascade,
  user_id       uuid references users(id),
  kind          text not null check (kind in ('capture', 'training')),
  status        text not null default 'live' check (status in ('live', 'processing', 'done')),
  recording_url text,                       -- Supabase Storage path to screen video
  el_conversation_id text,                  -- ElevenLabs conversation id (to pull transcript)
  started_at    timestamptz default now(),
  ended_at      timestamptz
);

-- Every UI event from the injected script / extension
create table events (
  id          bigserial primary key,
  session_id  uuid references sessions(id) on delete cascade,
  ts          timestamptz not null default now(),
  t_offset_ms integer,                      -- ms since session start, aligns with video
  type        text not null,                -- field_changed | save_clicked | page_view | look_at_screen
  page        text,                         -- e.g. 'fee_sheet', 'encounter'
  field       text,
  old_value   text,
  new_value   text,
  encounter_id text,                        -- OpenEMR encounter, to pull FHIR context
  frame_url   text,                         -- screenshot at this moment (Storage)
  payload     jsonb default '{}'
);
create index on events (session_id, t_offset_ms);

-- What the expert said (from ElevenLabs transcript, PHI-redacted)
create table transcript_segments (
  id          bigserial primary key,
  session_id  uuid references sessions(id) on delete cascade,
  speaker     text check (speaker in ('expert', 'agent')),
  t_start_ms  integer,
  t_end_ms    integer,
  text        text not null,
  off_record  boolean default false        -- expert said "off the record" -> excluded
);
create index on transcript_segments (session_id, t_start_ms);

-- ───────────── 2. WORK MAP (the knowledge that gets passed on) ─────────────
create table skills (
  id             uuid primary key default gen_random_uuid(),
  workflow_id    uuid references workflows(id) on delete cascade,
  source_session uuid references sessions(id),
  name           text not null,             -- "ER claims from out-of-network providers"
  trigger        jsonb not null,            -- machine-checkable conditions, see example below
  action         text not null,             -- what to do
  reason_quote   text,                      -- expert's own words
  guardrail      jsonb,                     -- condition that must block save
  guardrail_msg  text,                      -- "Hold on — Maria would stop here. Why?"
  clip_start_ms  integer,                   -- video clip of the expert doing it
  clip_end_ms    integer,
  status         text not null default 'draft' check (status in ('draft', 'approved', 'rejected')),
  version        integer not null default 1,
  approved_by    uuid references users(id),
  created_at     timestamptz default now()
);
create index on skills (workflow_id, status);

-- Example trigger / guardrail JSON:
-- trigger:   {"all": [{"field": "cpt", "op": "in", "value": ["99213","99214"]},
--                     {"field": "procedure_same_day", "op": "eq", "value": true}]}
-- guardrail: {"all": [{"field": "modifier", "op": "not_contains", "value": "25"}]}

-- Links a skill to the exact evidence it came from (auditability)
create table skill_evidence (
  skill_id    uuid references skills(id) on delete cascade,
  event_id    bigint references events(id) on delete cascade,
  segment_id  bigint references transcript_segments(id) on delete cascade
);

-- ───────────── 3. TEACH (trainee) ─────────────
-- Training sessions reuse `sessions` (kind = 'training') and `events`.

-- One row per time a skill came up for the trainee
create table attempts (
  id            uuid primary key default gen_random_uuid(),
  session_id    uuid references sessions(id) on delete cascade,
  skill_id      uuid references skills(id),
  trainee_id    uuid references users(id),
  prediction    text,                       -- answer to "what would you code this as, and why?"
  outcome       text not null check (outcome in ('correct', 'hinted', 'caught', 'missed')),
  blocked_save  boolean default false,
  tutor_message text,
  created_at    timestamptz default now()
);

-- Real-time tutor messages pushed to the side panel (Supabase Realtime on this table)
create table interventions (
  id          bigserial primary key,
  session_id  uuid references sessions(id) on delete cascade,
  skill_id    uuid references skills(id),
  kind        text check (kind in ('predict', 'explain', 'block', 'praise')),
  message     text not null,
  clip_url    text,
  created_at  timestamptz default now()
);

-- Rolled-up mastery per trainee per skill
create view mastery as
select trainee_id, skill_id,
       count(*)                                         as times_seen,
       count(*) filter (where outcome = 'correct')      as correct,
       count(*) filter (where outcome in ('caught','missed')) as mistakes,
       case
         when count(*) filter (where outcome = 'correct') >= 2
          and count(*) filter (where outcome in ('caught','missed')) = 0 then 'mastered'
         when count(*) filter (where outcome = 'correct') >= 1 then 'learning'
         else 'needs_practice'
       end as level
from attempts
group by trainee_id, skill_id;

-- What the tutor loads at the start of a training session
create view published_work_map as
select * from skills where status = 'approved';