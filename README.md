# hack-nation-7

AI Apprentice: a Chrome side panel that sits next to any web app. It captures an expert's judgment as a **Work Map** while they work, then uses that map to tutor newcomers on records they haven't seen.

Nothing is tied to one industry. Each **workflow** (for example "Approving expense reports") defines the fields of the records it works on. Claude proposes them from the first recorded session, and the expert can correct them.

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
