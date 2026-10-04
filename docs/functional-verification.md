# Functional verification

Use synthetic records. Start with **Use bounded apprentice** unchecked. The existing
conversational interview, full page context, Build Work Map and review callback remain the baseline.

## Verified on October 4, 2026

| Check | Result | Scope |
| --- | --- | --- |
| Python regression suite | 103 passed | Evidence, uncertainty, ownership, Gemini/OpenRouter routing, provider errors without credential disclosure, schema validation and protected interview recovery; unit dependencies mocked |
| Extension build and tests | 35 passed | Durable retries, automatic logger attachment, repeated production injection, partial frame access, tab scope, AudioWorklets, DOM/shadow fields, screenshot retention after AI failure and builds during a hung model request |
| PostgreSQL assertions | Passed | Real transaction rollback, idempotence, provenance, versioned review, snapshots, mastery and restricted RPCs |
| HTTP → PostgREST → PostgreSQL workflow | Passed | Synthetic capture, build, approval, teach-back, tutoring, guardrails, recovery and bounded answer evidence |
| Real Qwen connectivity and frame analysis | Passed | Synthetic boolean response in 0.7s; actual frame schema extracted three fields and generated a decision question in 9.5s; no real record sent |
| Real free-only Qwen build | Passed again on October 4 | Updated prompt preserves observed before/after values; isolated fixture uses actual Qwen for building and fixtures for other model boundaries |
| Real image-only Qwen preview | Blocked by HTTP 429 | Catalog advertises image input; synthetic image inference hit free capacity limits. Live image interpretation remains unverified |
| Gemini text connectivity | Passed | Actual Gemini 3.8 Flash returned the required JSON in 2.9 seconds; no real records sent |
| Gemini image-only screen preview | Passed on one explicit retry | Generated fixture yielded four fields, including expected amount/hold status, in 8.6 seconds. Initial request returned HTTP 503; no automatic retry loop |
| Selected Flash-Lite image preview | Passed | Actual gemini-3.5-flash-lite read the generated image with four extracted fields in 1.2 seconds, without DOM text |
| Gemini Work Map builder | Passed with Flash-Lite | Actual gemini-3.5-flash-lite generated evidence-backed skills in the isolated database; approval, teach-back, tutoring and guardrails passed. 3.8 Flash returned HTTP 503 twice for this build |
| Running backend readiness | Passed | API, migrated team schema and ElevenLabs signed-URL endpoint return successful responses; read-only checks |
| Team database migrations 003 and 004 | Applied | Protected backup in apprentice_backup_20261003; existing skills and workflows verified unchanged, three skills remain approved |
| Actual Chrome microphone interview | User confirmed | After reloading the extension, both expert and agent words appear in the log; generated skills and browser guardrail behavior still need hands-on checks |
| Public team sign-in | Implemented; not activated | Requires deployment configuration, administrator-provisioned membership and real account testing |

## Hands-on sequence

1. At chrome://extensions reload **AI Apprentice** loaded from extension/dist. Close
   and reopen its panel. Building alone does not reload Chrome.
   The header must show **0.1.4**. Use the actual website record as the active tab
   before starting; the panel binds that tab for the interview.
   **Check screen** takes one screenshot without a microphone or event logger. The
   picture remains visible even if its single AI request fails. This preview creates
   no observed actions or skills. Free model capacity limits still apply.
2. Select a workflow, enter your expert name, and start. Allow the microphone in the
   permission tab if prompted. Verify both speakers appear in the log.
3. Change one meaningful field in the original tab, then pause. Typing is committed after
   700ms or blur; text snapshots and observed actions reach the existing voice interviewer
   without a separate Qwen frame request by default. Workflow context is also shared.
   If optional automatic analysis is enabled, model requests settle for at least
   2.5 seconds and automatic requests are spaced by at least 8 seconds.
   Open **What the agent can read** and check the field values. Verify the question is relevant.
   Actions on other tabs must not enter this interview.
   The initial text snapshot is shared with the live voice agent before model reasoning,
   even if no decision question is generated. The panel shows the field/frame counts, capture time and **Shared with voice agent**.
   This is supplied text context, not continuous video or independent desktop access.
4. Check that **Actions saved** increases when you change a field or click a
   website button. Use **Return to interview tab** to work in the bound tab. A fresh
   interview verifies the logger and automatically attaches it if missing; a website
   refresh is no longer the normal startup requirement. Chrome must allow extension
   access to the site. Explain what you did, why, what changes the decision and what must hold before saving.
   Stop flushes pending typing and durable storage before debrief; optional screen reasoning may finish in the background. If a request fails, retry
   pending analysis or recover the saved interview.
   If you stopped before any action was recorded, use **Resume interview** to continue
   the same capture. Its existing transcript is retained. Published or built captures
   cannot reopen their evidence; start a new interview instead.
5. Build Work Map. Inspect conditions, action, expert quote and observed event. Approve
   accurate skills and reject inaccurate ones. Drafts are not published.
6. Review the teach-back, optionally listen, then explicitly confirm the exact revision.
   Edits or approval changes invalidate confirmation.
7. In Trainee use a matching synthetic case. Predict before saving; check an incorrect
   and a correct record. Missing required fields block. Repeated checks do not create mastery.
8. Test live Save/Submit on the same tab: violation, success, backend unavailable,
   panel closed and Enter. Recover a session with its pinned version; explicitly end
   teach mode to release its save gate.
9. In a fresh interview open **Interview options** and enable the bounded apprentice. Verify the question was recorded
   before linking an answer. For rephrased questions use explicit delivery/answer controls
   after inspecting the recorded turns.

Example: amount 120, no receipt, status pending. The expert holds expenses over 75
without receipts; the guardrail requires on_hold before saving. Inspect the actual
model output instead of assuming it generated this policy.

## Repeat checks

From backend/:

```sh
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m scripts.test_backend_ready
# One model request per check, using synthetic data only.
# Gemini must use a confirmed Google Free tier project with billing disabled.
.venv/bin/python -m scripts.check_reasoning --frame
# Optional image-only synthetic probe (requires Pillow); also consumes free quota
.venv/bin/python -m scripts.check_reasoning --image
```

From extension/ run `npm test`.

For a disposable PostgreSQL database, from the repository root with Docker running:

```sh
docker compose -p apprentice-audit -f scripts/compose.audit.yml up -d
docker compose -p apprentice-audit -f scripts/compose.audit.yml exec -T postgres psql -U postgres -d apprentice_audit -v ON_ERROR_STOP=1 -f /audit/bootstrap.sql -f /schema/base.sql -f /schema/migrations/003_apprentice_state.sql -f /schema/migrations/004_cohesive_workflow.sql -f /audit/cohesion.sql
```

Wait for PostgreSQL readiness if initial startup is still in progress. Apply base.sql
once to a fresh container; subsequent SQL runs use migration 004 and cohesion.sql only.
From backend/:

```sh
.venv/bin/python -m scripts.test_cohesive_flow
# Optional actual configured-model build, using the isolated synthetic fixture
.venv/bin/python -m scripts.test_cohesive_flow --live-model
```

The HTTP fixture keeps synthetic rows in the disposable container, never the team DB.
Stop with the same compose command followed by `down`; this project has no database volume.

## Limits

- Provenance identifies the source of a statement. Expert review checks its interpretation;
  neither establishes clinical correctness.
- Browser enforcement covers recognized Save/Submit clicks and ordinary form submissions,
  including Enter and requestSubmit. Direct form.submit(), arbitrary custom widgets and
  direct network writes can bypass DOM interception. This is not an EMR transaction firewall.
  Chrome also blocks extension access on protected pages.
- Free capacity can fail; errors retain facts and never silently switch providers/models.
  OpenRouter enforces zero-price routing. Gemini's allowed Flash models offer a Free
  tier, but only a Google Free tier project with billing disabled establishes no-cost
  billing. The dashboard showed Default Gemini Project on Free tier for this setup.
  Google may use free-tier content to improve products; use synthetic demo records.
  Local queues contain page context/transcripts until a successful build; canonical evidence
  remains in Supabase afterward.
- Public account testing, deployment and microphone testing are separate acceptance checks.
  The service key stays backend-only. See [architecture and setup](agentic-backend.md).
