# Supabase integration handoff

Reviewed `origin/sebastian-torres` at commit
`b3f2f46f9eb4d09ff5c0d929b6aa12fa5911e8d3` on October 3, 2026.
This is a source-code review, not a live database verification. The branch was
fetched for inspection; it has not been merged into `vincent/AgentsWorflow`.

## What is verified locally

Steps 1–3 have 37 passing offline tests. The event module normalizes browser messages
and vision observations. `agent.process_event` retains events in memory and dispatches
validated decisions through bounded tools, with pause and confirmation checks.
There is no new durable event logger or browser-to-agent capture connection yet.
Agent tests use a fake reasoning callable; neither live Qwen nor live Claude behavior
has been verified. Supabase and voice have not been connected to these modules.

## What Sebastian added

- [Async Supabase store](https://github.com/sebastiantorrescodes/hack-nation-7/blob/b3f2f46f9eb4d09ff5c0d929b6aa12fa5911e8d3/backend/app/store.py): workflows, cases, capture sessions, transcript turns, frames, skills, evidence, training attempts and interventions.
- [Database schema](https://github.com/sebastiantorrescodes/hack-nation-7/blob/b3f2f46f9eb4d09ff5c0d929b6aa12fa5911e8d3/scripts/db.sql): `users`, `workflows`, `cases`, `sessions`, `events`, `transcript_segments`, `skills`, `skill_evidence`, `attempts`, `interventions`, plus `mastery` and `published_work_map` views.
- [Migrations](https://github.com/sebastiantorrescodes/hack-nation-7/tree/b3f2f46f9eb4d09ff5c0d929b6aa12fa5911e8d3/scripts/migrations): additions for backend columns and domain-agnostic records. Fresh databases use `db.sql`; existing databases need the applicable migrations in order.
- [Domain-agnostic models](https://github.com/sebastiantorrescodes/hack-nation-7/blob/b3f2f46f9eb4d09ff5c0d929b6aa12fa5911e8d3/backend/app/models.py): workflow-defined fields and cases replace hardcoded billing fields and claims.
- Backend startup calls `await store.init()`, initializes the async client, and seeds a demo. Configuration needs `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` on the backend; the secret stays out of the extension.
- New skills start as `draft`; approved skills are published to trainees. Editing/review routes and an expert UI are already present.

## Mapping our modules to the existing schema

| Our data | Existing Supabase destination | Required adapter behavior |
|---|---|---|
| Capture/workflow context | `sessions`, `workflows`, `users` | Retain database UUIDs and workflow field definitions. Map capture/debrief/finished to live/processing/done deliberately. |
| `ObservedEvent` | `events` | Store type, page, target as field, values, and the full observation in payload. Preserve source, client event ID, confidence, coordinates and parent observation ID. |
| Event timing | `events.ts`, `t_offset_ms` | Convert known epoch `timestamp_ms` to UTC; derive session-relative offsets using a defined clock relationship. Do not treat epoch milliseconds as relative offsets or invent unknown times. |
| Queued question | New question record or explicit transcript metadata | Preserve question ID, event reference, phase and question type. Existing transcript rows alone do not store these associations. |
| `ExpertAnswer` | Expert `transcript_segments` plus explicit answer/question mapping | Preserve exact text, answer ID, question ID, event reference, and transcript segment ID. The model must not create expert answers. |
| Complete `WorkflowStep` | `skills` with `status='draft'` | Map title/action/reason to name/action/reason_quote. Keep partial proposals separate until required fields are known. |
| Step event/answer references | `skill_evidence` | Link the actual database event and expert transcript segment IDs. Preserve client-ID-to-database-ID mappings. |
| Expert step confirmation | `skills.status`, `approved_by`, `version` | Only explicit expert review produces approved status. An edit must return the skill to draft and clear approval. |
| Teach-back approval | Additional explicit session confirmation data | Persist the approving expert and the reviewed revision; current session status alone does not represent this approval. |

Events and transcript segments use bigint database IDs, while our in-memory event,
question, and answer IDs are strings. Store the original IDs and map them to inserted
rows. Add a session-scoped uniqueness mechanism for client event IDs so retries do
not create duplicate events. Saving and reloading must preserve the same provenance.

Our step proposals currently contain natural-language actions and guardrails.
Sebastian's skills additionally need structured trigger/guardrail conditions over
workflow fields for deterministic tutoring. Do not fabricate those conditions while
mapping text, or treat a missing guardrail as an empty condition list. Add proposed,
expert-reviewed structured conditions when capture integration needs them.
`skills.action` is NOT NULL, so incomplete proposals cannot be inserted as complete
skills without a deliberate draft representation.

## Changes required before connecting the agent

1. **Reuse the existing store rather than build a second database layer.** Add small
   repository functions for normalized events, explicit question/answer records,
   draft proposals, exact evidence, and confirmations. Keep queries out of agent logic.
2. **Extend both write and read adapters.** `record_frame` currently writes the older
   `ScreenEvent` shape; `_capture` reconstructs that shape. Simply replacing payloads
   with `ObservedEvent` JSON would lose new fields on reload. Preserve normalized
   events through a dedicated adapter while keeping legacy routes compatible.
3. **Use exact evidence.** `_set_evidence` currently writes transcript references but
   not `event_id`. `_segment_for` falls back to the nearest timestamp if the quote
   is not found. Use explicit mappings instead; unknown evidence must remain unknown.
   `_segments` also currently includes off-record rows. Exclude off-record material
   from proposal generation and evidence, and invalidate affected knowledge if its
   evidence is taken off record later.
4. **Align review behavior.** `update_workmap` changes existing skill content/version
   without clearing approved status or approved_by. Reset approval after content or
   evidence edits, as our agent tools already do. The approval endpoint currently
   attributes approval to the recording expert; the application must validate the
   actual reviewer when wiring explicit approval.
5. **Persist session reasoning state.** Add the missing question/answer links,
   partial drafts and teach-back confirmation data through a small migration.
   Define which rows are authoritative so a stale snapshot cannot restore an old
   approval. Group related writes atomically where practical to avoid partially
   saved skills/evidence.
6. **Replace the old combined frame reasoning at integration time.** Sebastian's
   capture route still asks one model (now Qwen through the shared OpenRouter
   adapter) to analyze screenshots and choose questions in one call. Step 6 should instead normalize DOM events, use Qwen only when needed,
   call our apprentice for reasoning, and let ElevenLabs deliver the chosen speech.

## Validation needed after integration

- Save/reload a normalized event without losing IDs, unknown values, locations or timing.
- Retry the same event without inserting another row.
- Preserve question → expert answer → event → proposed skill evidence exactly.
- Verify off-record evidence is excluded, without nearest-transcript substitutions.
- Verify editing an approved skill removes it from the published Work Map until reapproved.
- Restart the backend and recover pending questions, drafts and confirmation state.
- Finish only after expert-confirmed steps and teach-back; verify against a disposable
  Supabase project before calling persistent logging or the full capture loop working.

Next staged work is voice (Step 4), then Supabase memory (Step 5), then capture wiring
(Step 6). This review does not advance those implementation stages automatically.
