# Test model-backed voice capture on your UI

The Expert capture panel keeps the original live ElevenLabs conversation, full
EMR page snapshots, optional screenshots, transcript logging, and Build Work Map
button. The ordinary interview sends workflow, page text and observed actions directly
to the existing ElevenLabs voice agent, without extra automatic Qwen frame requests.
Optional automatic analysis and explicit screenshot interpretation use your co-founder's
shared Gemini/OpenRouter adapter. Work Maps still
write draft skills into the existing Supabase tables for expert review and trainee
practice. The manual playback/recording replacement has been removed from this panel.

An optional checkbox enables bounded apprentice interview questions behind that same interface.
Both interview paths share durable evidence and Work Map review. Record extraction and
prediction grading use the selected provider; mastery is calculated deterministically.

## Local setup

1. Configure `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` in `backend/.env`.
   Keep the ElevenLabs key there too; never commit this file. Add GEMINI_API_KEY
   from an AI Studio Free tier project (billing disabled) to select Gemini automatically.
   The default is gemini-3.5-flash-lite, tested for screenshot reading and Work Map
   building. Restart the backend after changing credentials or provider settings.
   Explicit LLM_PROVIDER=openrouter keeps the existing OpenRouter configuration instead.
2. Apply migrations 003 and 004 after the existing schema. A fresh database runs
   `scripts/db.sql`, then 003 and 004; the base already includes 001 and 002.
   Both standard and bounded capture now require these migrations.
3. From `backend`, run:

   ```sh
   .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

4. Reload the extension at `chrome://extensions`. Load `extension/dist` as an
   unpacked extension if it isn't already installed. Close and reopen its panel;
   verify version **0.1.4**. Startup attaches the event logger to already-open
   websites automatically, subject to Chrome site access.
5. Open your test UI in Chrome, open the AI Apprentice side panel, enter your
   expert name, and select or create a workflow in Expert.
6. Under **Interview options**, leave **Use bounded apprentice for interview questions** unchecked to reproduce the original
   demo. Start interview, allow the microphone if prompted, return to your UI,
   and change a field. The conversational agent asks at the next natural pause.
   Leave **Add automatic screen analysis** unchecked initially. **Check screen**
   can preview one screenshot before starting; its image remains visible if the
   AI request hits a free quota limit. Preview interpretations are not evidence.
7. Stop the interview and click **Build Work Map**. Draft skills appear in the
   existing review section; approved skills are available in Trainee.
8. To test Qwen separately, use a fresh capture panel/session and open **Interview options** and check the optional
   bounded box before starting. The selected provider receives structured UI events, the page snapshot,
   workflow metadata and recent transcript. The same live voice conversation speaks
   the question. Screenshots remain available only in the standard capture mode.

The bounded coordinator's queued question gets an explicit ID. The voice transcript must match its
text before the next expert response can be linked as evidence. Rephrased or
unverified questions are not guessed: plain transcript storage remains available.
For a verified rephrasing, explicit delivery and answer-link controls let the expert
confirm the recorded turns. Delivery and answer links are committed with checkpoint revision checks.
Model proposals stay unconfirmed and are not directly published into skills. The
existing Work Map builder remains the route into draft skills and expert review.
Either provider may return 429; wait for capacity before Retry pending screen analysis rather than a paid
fallback. The optional model does not silently replace the default flow.

## Files

- `extension/src/content.ts`: structured events with stable client IDs.
- `extension/src/sidepanel/Capture.tsx`: live conversational capture, optional Qwen mode, and Build Work Map callback.
- `backend/app/agent/interview.py`: Qwen adapter with full page context and verified question/answer links.
- `backend/tests/test_conversational_capture.py`: default-path and optional-mode regression checks.
- `backend/app/routes/apprentice.py`: validates capture requests and dispatches bounded reasoning.
- `backend/app/agent/persistence.py`: uses the existing Supabase client for agent checkpoints.
- `scripts/migrations/003_apprentice_state.sql`: checkpoint table and legacy RPC.
- `scripts/migrations/004_cohesive_workflow.sql`: canonical ingestion, answer links, atomic builds/review, tutor snapshots and mastery.
- `backend/tests/test_capture_integration.py`: route tests with mocked database and providers.

Normalized events are stored in the existing `events` table; speech text goes to
`transcript_segments`. Answer IDs and event references live on the transcript rows;
questions and provisional steps live in `apprentice_states.state`. Checkpoints do not
duplicate raw events or answers. Raw microphone recordings are not stored.

Review and teach-back now use the same skill publication flow for both paths. See
[architecture](agentic-backend.md) and [verification](functional-verification.md).
Local integration tests do not prove a live microphone session or public deployment.

## AudioWorklet startup errors

The extension build packages the installed ElevenLabs SDK's `rawAudioProcessor`
and `audioConcatProcessor` in `dist/worklets`. Capture passes their
`chrome.runtime.getURL` URLs through the SDK's `workletPaths` option. The extension
keeps its default Manifest V3 security policy; it does not enable blob/data scripts.

If an older installed build reports **Failed to load the audioConcatProcessor
worklet module**, rebuild with `npm run test:voice` from `extension`, click Reload
on **AI Apprentice** at `chrome://extensions`, close and reopen the side panel,
and retry Start interview. Reopening clears the SDK's previous worklet URL cache.
The rebuilt header and extension manifest show version **0.1.4**. Before creating
a capture session, the browser loads both processors as an audio startup check.
If that fails, its error is labeled **Audio startup check (0.1.4)**; the original
generic SDK error by itself indicates that the older code may still be running.
The check validates the packaged files and configuration; a real interview is
still needed to verify microphone input, voice playback and transcription.

On Chrome, the SDK uses the supported sample-rate constraint for microphone
audio. Its separate resampling fallback for browsers without that constraint is
not packaged by this fix; such browsers would need a local resampler too.
