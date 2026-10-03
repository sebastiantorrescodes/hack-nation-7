-- Columns the FastAPI backend needs on top of the initial schema (scripts/db.sql).
-- Run once in the Supabase SQL editor. Safe to re-run.

-- Capture sessions: the Work Map built from a capture session is the set of skills with
-- source_session = that session, so the session row also holds the Work Map's summary.
alter table sessions add column if not exists summary text;               -- Work Map summary (capture)
alter table sessions add column if not exists screen_summary text;        -- latest screen description, context for the next frame (capture)

-- Training sessions
alter table sessions add column if not exists capture_session_id uuid references sessions(id);  -- Work Map being practiced
alter table sessions add column if not exists claim jsonb;               -- the claim as it arrived; triggers are evaluated on this
alter table sessions add column if not exists matched_skill_ids uuid[] default '{}';

-- what kind of action the skill takes; see skills.action_kind in db.sql
alter table skills add column if not exists action_kind text;

-- The short transcript excerpt that supports the skill (segment_id points at the full segment)
alter table skill_evidence add column if not exists quote text;
