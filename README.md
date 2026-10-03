# hack-nation-7

AI Apprentice: a Chrome side panel that sits next to any web app. It captures an expert's judgment as a **Work Map** while they work, then uses that map to tutor newcomers on records they haven't seen.

Nothing is tied to one industry. Each **workflow** (for example "Approving expense reports") defines the fields of the records it works on. Claude proposes them from the first recorded session, and the expert can correct them.

## Judge access requirement

The final MVP must be accessible from a shareable link, and judges must also be able
to run it on their own computer. Delivery must include a reproducible setup with
documented prerequisites, environment configuration, and start commands. The app
must not depend on the developer's absolute paths, cached models, or private files.
The delivery plan is a hosted demo link plus local setup instructions, unless a different
link experience is requested.
This is a delivery requirement; the current template is not a published, judge-ready demo.

## Staged apprentice implementation: Step 1

The new local vision module is independent of the existing capture and tutor routes below.
It only converts a screenshot into a validated `ScreenObservation`; it does not interview
the expert, create rules, or store screenshots. Its real local-model screenshot test
is still pending. Step 2 proceeded at the user's explicit request and works independently
of model loading.

```text
backend/
  app/vision/
    __init__.py          # Identifies the vision package.
    provider.py          # Defines analyze_screenshot(image) -> ScreenObservation.
    schemas.py           # Validates screen descriptions, visible elements and boxes.
    qwen_provider.py     # Loads local Qwen3-VL and parses its observation JSON.
  scripts/
    analyze_screenshot.py # Runs an independent screenshot test from the command line.
  tests/
    test_vision.py       # Checks validation and the adapter with fake inference APIs.
  requirements-vision.txt # Lists only the optional local vision dependencies.
```

Use Python 3.12 for this module (the system Python on some Macs is 3.9):

```sh
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-vision.txt
.venv/bin/python -m unittest discover -s tests -v
```

For the offline tests alone, installing `pydantic>=2.7,<3` and `pillow>=10.4`
is sufficient; PyTorch and Transformers are loaded only for real inference.

Run a live test with your own sandbox screenshot and downloaded Hugging Face model:

```sh
.venv/bin/python -m scripts.analyze_screenshot /absolute/path/screenshot.png \
  --model-path /absolute/path/Qwen3-VL-2B-Instruct
```

The model directory must contain its Hugging Face configuration, processor/tokenizer
files, and weights. A Qwen desktop installer, text-only Qwen model, or GGUF file is not
that directory. All model loading uses `local_files_only=True` and
`trust_remote_code=False`; a missing checkpoint fails instead of downloading weights
or returning a mock success. You can also set `QWEN_MODEL_PATH` in your shell and omit
`--model-path`. Use `--device cpu`, `mps`, or `cuda` to override automatic device selection.

Each visible element retains its label, value, and optional bounding box together.
The `labels`, `values`, and `bounding_boxes` Python properties expose aligned lists;
the JSON stores those facts once under `visible_elements`. Box coordinates range
from 0 to 1 relative to the original image. Unknown facts use `null`.
`possible_event` is normally `null`: one image cannot establish a field change or click.
It may describe an explicit visible activity message. Model confidence is an estimate,
not a calibrated score or permission to confirm expert knowledge.

The unit tests use fake model APIs, so passing them does **not** establish actual Qwen
accuracy. For live verification, run the command, verify labels/values and box locations
against the screenshot, and confirm that it does not invent hidden facts or reasoning.
The provider follows the [Hugging Face Qwen3-VL inference API](https://huggingface.co/docs/transformers/en/model_doc/qwen3_vl).

Current validation: all eight offline tests pass, and the standalone command's help
works. Only the lightweight test dependencies are installed in the development
environment. The real-model test remains pending the local Qwen3-VL checkpoint and
installation of the full vision requirements.

## Staged apprentice implementation: Step 2

Browser actions and vision observations now share the `ObservedEvent` format.
Normalization is standalone: it does not call models, ask questions, store events,
or change the existing capture routes. Connecting these modules to capture is Step 6.

```text
backend/
  app/events/
    __init__.py       # Identifies the event normalization package.
    schemas.py        # Defines ObservedEvent and validates values and locations.
    normalizer.py     # Converts browser messages and vision observations to events.
  tests/
    test_events.py    # Tests browser compatibility, provenance and vision uncertainty.
```

`normalize_browser_event(payload)` accepts a dictionary with `event_type` set to
`click`, `field_change`, `navigation`, or `submit_attempt`. Optional fields include
`event_id`, `source_event_id`, `timestamp_ms`, `target`, `old_value`, `new_value`, `page`,
`description`, `x_norm`, `y_norm`, `bounding_box`, and `confidence`.
A field change requires a target and a known new value; `""` means a cleared field,
while `null` means unknown. Coordinates are normalized to 0–1 and must be supplied
together. Missing locations remain unknown. Timestamps are milliseconds, not seconds.
Confidence defaults to 1 for a direct browser report; it is not confidence in the
correctness of the user's decision.

The adapter also accepts the current extension's `{type: "ui-action", text, frame, t}`
messages for `changed "Label" to "Value"` and `clicked "Label"`, plus
`{type: "save-attempt"}`. It retains the frame path and millisecond timestamp. The
current extension does not capture old values or coordinates, so those remain `null`.
Ambiguous legacy text with embedded quotes is rejected; use structured events for it.
A click on a button labeled Save remains a click; only a submission report becomes
`submit_attempt`.

`normalize_vision_observation(observation, page=..., observation_id=..., timestamp_ms=...)`
returns one `screen_observation` event per visible element. It derives the center
coordinates from a known box and retains the full box and model confidence. A visible
status description produces a separate observation. An empty screen still produces
its page-description observation. It never infers an old value, click, or field change
from a single screenshot. Each output has an event ID and source; supplying
`observation_id` preserves the parent observation reference in `source_event_id`.

Example, from Python running in `backend/`:

```python
from app.events.normalizer import normalize_browser_event

event = normalize_browser_event({
    "event_type": "field_change", "target": "Modifier",
    "old_value": "", "new_value": "25", "page": "Billing",
    "x_norm": 0.62, "y_norm": 0.41, "confidence": 0.96,
})
print(event.model_dump_json(indent=2))
```

Run `.venv/bin/python -m unittest discover -s tests -v` from `backend/`.
All 19 tests pass: 11 normalization tests and 8 offline vision tests. No additional
dependencies are needed for Step 2 beyond Pydantic. The full suite also uses Pillow
for the Step 1 tests.

## Staged apprentice implementation: Step 3

The new apprentice receives an `ObservedEvent`, a `WorkMapState`, and a
`CaptureContext`. It asks the reasoning LLM for one bounded decision and dispatches
only that decision to explicit tools. This module is separate from the existing
capture/tutor routes and their older Work Map format; integration remains Step 6.

```text
backend/
  app/agent/
    __init__.py       # Identifies the staged agent package.
    schemas.py        # Defines decisions, expert evidence and Work Map state.
    apprentice.py     # Applies pause policies and executes one bounded decision.
    tools.py          # Implements in-memory tools and expert-only review operations.
    prompts.py        # Defines reasoning instructions, context and the output schema.
  tests/
    test_agent.py     # Tests the brain using a fake LLM and explicit expert actions.
```

Call `await process_event(event, state, context)` to use the existing Claude
`app.llm.structured` adapter, or supply `reason=your_async_callable` for an alternate
reasoning provider or an offline test. The callable receives `system`, `content`,
`schema`, `max_tokens`, and `effort` as keyword arguments and returns a decision
dictionary. It is never the Qwen vision provider.

The six decisions are `ask_question`, `save_step`, `update_step`, `wait`,
`start_debrief`, and `finish_session`. All decisions contain `action`, `reason`,
`question`, `step`, and `step_id`; unused arguments must be `null`. The schema
rejects arbitrary tool names, invented confirmation flags, and mismatched arguments.
The API schema omits unsupported string-length constraints, while the original
Pydantic validation still enforces them locally, following the
[Claude structured-output schema limitations](https://platform.claude.com/docs/en/build-with-claude/structured-outputs#json-schema-limitations).

The application must report a real pause with `CaptureContext(expert_paused=True)`.
Unknown pause state, typing/reading (`expert_busy=True`), speech, or a pending answer
causes the agent to wait without calling the model. The dispatcher checks activity
again after reasoning, so activity resumed during the request prevents an interruption.
Timing detection is the capture/voice caller's responsibility; the brain does not
guess pauses from screenshots. A `task_finished=True` signal starts debrief at a
pause once no answer is pending; the model cannot invent task completion.

The limited tools are `ask_expert`, `save_workmap_step`, `update_workmap_step`,
`get_workmap`, `get_missing_fields`, `start_debrief`, and `finish_session`.
Questions are queued in memory; spoken delivery belongs to Step 4. Only the
application may call `record_expert_answer`, `confirm_step`, and
`confirm_teach_back` after actual expert input. Those operations are not available
as model decisions. The application must enforce the expert's identity and capture
explicit approval when these operations are wired to a UI or voice service.

Every proposed step references recorded event IDs and actual expert-answer IDs.
The tools reject missing or unrelated evidence. These checks establish provenance,
not semantic truth: the expert still needs to review the proposed interpretation.
Unknown action, reason or guardrails remains `null`; an empty guardrail list means
the expert explicitly reported none. Missing fields and unconfirmed proposals are
reported for follow-up. Editing a confirmed step resets it to proposed and clears
teach-back approval. New questions or answers also clear teach-back approval.
A session can finish only in debrief, with a nonempty, complete Work Map, every
step explicitly approved by the session's expert, no pending answer, and a separate
expert-confirmed teach-back.

All state is in memory for this stage. Supabase persistence is Step 5. The current
module does not implement spoken teach-back or the full challenge's debrief delivery;
those are handled when the capture and voice loop is connected.

Run `.venv/bin/python -m unittest discover -s tests -v` from `backend/`.
All 37 offline tests pass: 18 agent tests, 11 normalization tests, and 8 vision tests.
Agent tests need only Pydantic; the full suite also uses Pillow. A live reasoning
call additionally needs `backend/requirements.txt` installed and the existing
Claude adapter configured with credentials. No paid API call was made during these
tests, so live model behavior is still unverified. Step 4 has not been started.

## How it fits together

| Part | Folder | What it does |
|---|---|---|
| Chrome extension | [extension/](extension/) | Side panel UI (React + Vite) with an **Expert** tab and a **Trainee** tab. A content script runs in every frame of every page. It reports field changes and button clicks (never passwords) while an interview is recorded, and in tutor mode it holds Save/Submit clicks until the guardrail check passes. |
| Backend | [backend/](backend/) | FastAPI on Supabase. Calls Claude to spot decision points, build the Work Map (and the workflow's record fields), read records off the page, grade predictions and write mastery reports. Gives the side panel a signed URL for the ElevenLabs agent. |
| Database | Supabase | Schema in [scripts/db.sql](scripts/db.sql); existing databases apply [scripts/migrations/](scripts/migrations/) in order. |
| Voice | ElevenLabs Conversational AI | Interviews the expert. When Claude spots a decision point, the side panel sends the agent a contextual update with the "why" question to ask. |
Flow:
1. **Capture**: the expert picks a workflow and works in their app → the content script reports actions → the side panel snapshots the page (and optionally takes a screenshot) → Claude decides whether it was a decision point → the voice agent asks why.
2. **Work Map**: Claude turns the timeline of actions and transcript into skill records, and adds any record fields they need to the workflow. Each skill has a trigger (conditions on record fields), an action, the expert's explanation and a guardrail. The expert approves the skills trainees should learn.
3. **Tutor**: the trainee picks a published workflow and a practice case (or the record open in the app) → triggers are matched in code → the learner predicts each decision → saving runs the guardrails in code → a mastery report at the end.

A sample workflow ([backend/app/seed/demo_workflow.json](backend/app/seed/demo_workflow.json), expense report review) with practice cases is seeded on first start, so the tutor works before you've captured anything.

## Running the app

### 1. Backend

```sh
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env, then fill in the keys
uvicorn app.main:app --reload --port 8000
```

`.env` needs `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` (the secret key), `ANTHROPIC_API_KEY` (or run `ant auth login`), `ELEVENLABS_API_KEY` and `ELEVENLABS_AGENT_ID`. Apply [scripts/db.sql](scripts/db.sql) in the Supabase SQL editor first.

### 2. ElevenLabs agent

Create an agent in the ElevenLabs dashboard and put its ID in `.env`. Turn on authentication (signed URLs) so the API key stays on the backend. Suggested system prompt:

> You are interviewing {{expert_name}}, an experienced professional, while they do their everyday work in their software. Your goal is to capture *why* they make each decision so newcomers can learn it. Keep questions short and natural, one at a time, and never interrupt mid-task. When you receive a contextual update about something they just did on screen, ask that question at the next natural pause, then follow up once if their reason is vague ("What would happen if you didn't?", "Is that always true, or only in some cases?"). Don't give advice yourself.

First message, for example: "Hi {{expert_name}}, go ahead and work like normal. I'll ask a quick why now and then."

### 3. Extension

```sh
cd extension
npm install
npm run build        # or: npm run dev  (rebuilds on save)
```

Then in Chrome go to `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and pick `extension/dist`. Open the app you work in and click the extension icon to open the side panel. After a rebuild, click the reload icon on the extension card and reload the app's tab.

The extension can read every site (`<all_urls>`), so Chrome warns about that when you load it. It only sends page contents to the backend while an interview is being recorded, a practice case is being read, or a tutor save check runs.

The first time you start an interview, a tab opens asking for microphone access. Chrome can't show that prompt inside a side panel. Allow it, close the tab, and press Start again.

## Optional: OpenEMR sandbox (Docker)

The [openemr-sandbox/](openemr-sandbox/) folder runs OpenEMR (an open-source medical records app) with a MariaDB database. It's just one example app to record and practice in; nothing in the extension or backend depends on it.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine with the Compose plugin), running

### Start

```sh
cd openemr-sandbox
docker compose up -d
```

The first start takes about 3–5 minutes while OpenEMR sets up its database. To watch progress:

```sh
docker compose logs -f openemr
```

### Open

- URL: http://localhost:8300
- Username: `admin`
- Password: `pass`

The port only listens on `127.0.0.1`, so the app is reachable from your own machine only.

### Stop

```sh
docker compose down
```

Data is kept in Docker volumes, so the next `up` is fast. To wipe everything and start fresh:

```sh
docker compose down -v
```
# Qwen API agent

The staged apprentice now uses free-only OpenRouter reasoning by default.
See [setup and live test instructions](docs/qwen-api-setup.md). Existing web
capture routes still use the legacy Claude flow until the agent is wired in.

Try the [Qwen + ElevenLabs voice demo](docs/voice-demo.md) for spoken questions,
recorded answers, and evidence-backed proposals without the legacy Claude routes.
