# Capture and reasoning reliability

The working ElevenLabs interview → canonical Supabase evidence → Build Work Map →
draft skills → expert review → trainee flow remains the baseline. These changes stay
inside the existing capture, page, event and model modules; no replacement agent framework.

## Findings

- Native `change` events missed typing until blur, and custom controls were often invisible.
- `offsetParent === null` excluded some visible fixed controls. Open shadow roots and
  editable ARIA widgets were missing from snapshots.
- An optional screenshot error prevented text context from reaching the interviewer.
- Screen capture and model reasoning shared a serialized loop. A slow response made
  newer context wait; a shared timer also let bounded answer follow-ups cancel action analysis.
- Qwen itself responded to synthetic checks: connectivity in 0.7s and the real frame
  schema in 9.5s, with three extracted fields and a decision question. This establishes
  availability at test time, not continuous availability or real-record accuracy.
- Startup only checked for an existing logger; it did not attach one to already-open
  websites. Version 0.1.4 verifies each accessible frame and injects packaged listeners
  when needed. The built script can be injected repeatedly without duplicate listeners
  or top-level declaration collisions. Site access failures remain explicit.
- A later image-only Qwen check returned HTTP 429. Gemini's generated image test
  subsequently passed in 8.6 seconds after one explicit retry of HTTP 503. Model
  capacity is a separate failure boundary from browser capture; one successful
  fixture does not prove availability or real-record accuracy.
- Selected gemini-3.5-flash-lite passed the image-only fixture in 1.2 seconds and
  the actual Work Map builder in the isolated capture-to-teaching flow. It is the
  Gemini default because 3.8 Flash returned HTTP 503 twice on that build. The
  original voice, evidence and skill publication flow remains intact; there is
  no automatic model/provider fallback.

## Decisions and tradeoffs

| Choice | Benefit | Cost / limit |
| --- | --- | --- |
| Automatically attach the packaged logger | Ordinary websites no longer require a refresh after installing/reloading the extension | Chrome site permission is still required; protected pages and restricted frames remain inaccessible |
| Default interview sends workflow, text and observed actions directly to ElevenLabs | Avoids a second model request on every action; preserves durable evidence and Build Work Map | Live voice uses ElevenLabs credits and its configured model; explicit preview/build calls still depend on OpenRouter capacity |
| Explicit Check screen outside an interview | One native screenshot; no microphone, logger or session required; picture remains visible if AI fails | One vision request per click; screenshot interpretation cannot establish observed changes or expert reasons and cannot create skills |
| Text-first snapshots; optional JPEG | Fast, readable field values; image failures leave text capture working | Closed shadow roots, canvas and inaccessible frames can remain unreadable; images help only standard screen analysis, not the voice agent directly |
| Commit typing after 700ms, on blur/change or Stop | Captures edits without per-keystroke messages; repeated final values deduplicate | Intermediate keystrokes are deliberately not evidence; old values are recorded only when observed before editing |
| Separate snapshot and reasoning tasks | New context reaches voice while earlier reasoning is pending | Durable writes still wait for database availability; local retries retain the same identities |
| Latest screen for reasoning; all raw actions retained | Reduces stale questions and free requests; full canonical history remains available for building | Rapid transient screens may receive no individual model analysis; last ten canonical actions also accompany standard reasoning |
| At least 8 seconds between automatic frame requests | Limits automatic request frequency; no automatic failure loop or paid fallback | A follow-up analysis can wait; this is not a provider quota guarantee or a global limiter across routes/users |
| 45s read timeout for low-effort reasoning; 180s for builds | Live failures surface sooner; a full-session build gets more time | Timeouts indicate unavailable enrichment, not that saved evidence is lost |
| Stop waits for facts, not optional reasoning | Interview can stop and build from durable evidence despite a hung frame model | An already-running analysis can finish in the background; stale/late questions are suppressed |
| Field/frame counts, timestamp, text preview, refresh and separate errors | User can verify what is actually captured and distinguish no question from provider failure | Snapshot quality counts describe readable content, not semantic or clinical correctness |

## Acceptance evidence

- 103 Python regression tests and 35 extension tests pass; production build is 0.1.4.
- Real HTTP → PostgREST → PostgreSQL synthetic flow passes capture, replay, before/after
  values, building, approval, teach-back, tutoring, guardrails, recovery and mastery.
- Chrome microphone and complete record-specific acceptance still require reloading
  the extension and trying the actual test app. A build does not reload an installed extension.
- No new migration or team database write was needed for this repair.

See [hands-on verification](functional-verification.md). The bounded coordinator,
optional automatic screen analysis, explicit screenshot interpretation and Work Map
builder use the shared Gemini/OpenRouter adapter. A Gemini key selects Gemini by
default; its project must be Free tier with billing disabled. Explicit provider
selection preserves OpenRouter zero-price routing. The ordinary live ElevenLabs interview
receives raw context directly and has its own configured model. There are separate
model boundaries, but a single evidence and skill publication flow.
