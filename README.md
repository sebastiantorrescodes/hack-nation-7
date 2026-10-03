# hack-nation-7

Billing Apprentice: a Chrome side panel that sits next to OpenEMR. It captures a senior biller's judgment as a **Work Map**, then uses that map to tutor new hires on claims they haven't seen.

## How it fits together

| Part | Folder | What it does |
|---|---|---|
| Chrome extension | [extension/](extension/) | Side panel UI (React + Vite). A content script runs in every OpenEMR frame. It reports field changes and button clicks, and in tutor mode it holds the Save click until the guardrail check passes. |
| Backend | [backend/](backend/) | FastAPI. Calls Claude to spot decision points, build the Work Map, read claims off the page, grade predictions and write mastery reports. Gives the side panel a signed URL for the ElevenLabs agent. |
| Voice | ElevenLabs Conversational AI | Interviews the expert. When Claude spots a decision point, the side panel sends the agent a contextual update with the "why" question to ask. |
| OpenEMR | [openemr-sandbox/](openemr-sandbox/) | The real EHR, running locally on port 8300. |

Flow:
1. **Capture**: the expert works in OpenEMR → the content script reports actions → the side panel snapshots the page (and optionally takes a screenshot) → Claude decides whether it was a decision point → the voice agent asks why.
2. **Work Map**: Claude turns the timeline of actions and transcript into skill records. Each one has a trigger (conditions on claim fields), an action, the expert's explanation and a guardrail.
3. **Tutor**: Claude reads the claim → triggers are matched in code → the learner predicts each decision → pressing Save in OpenEMR runs the guardrails in code → a mastery report at the end.

A hand-written demo Work Map ([backend/app/seed/demo_workmap.json](backend/app/seed/demo_workmap.json)) and four practice claims are included, so the tutor works before you've captured anything.

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

`.env` needs `ANTHROPIC_API_KEY` (or run `ant auth login`), `ELEVENLABS_API_KEY` and `ELEVENLABS_AGENT_ID`.

### 2. ElevenLabs agent

Create an agent in the ElevenLabs dashboard and put its ID in `.env`. Turn on authentication (signed URLs) so the API key stays on the backend. Suggested system prompt:

> You are interviewing {{expert_name}}, a senior medical biller, while they work claims in OpenEMR. Your goal is to capture *why* they make each decision so new billers can learn it. Keep questions short and natural, one at a time, and never interrupt mid-task. When you receive a contextual update about something they just did on screen, ask that question at the next natural pause, then follow up once if their reason is vague ("What would happen if you didn't?", "Is that true for every payer?"). Don't give billing advice yourself.

First message, for example: "Hi {{expert_name}}, go ahead and work your claims like normal. I'll ask a quick why now and then."

### 3. Extension

```sh
cd extension
npm install
npm run build        # or: npm run dev  (rebuilds on save)
```

Then in Chrome go to `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**, and pick `extension/dist`. Open OpenEMR at http://localhost:8300 and click the extension icon to open the side panel. After a rebuild, click the reload icon on the extension card and reload the OpenEMR tab.

The first time you start an interview, a tab opens asking for microphone access. Chrome can't show that prompt inside a side panel. Allow it, close the tab, and press Start again.

## Running the OpenEMR sandbox (Docker)

The [openemr-sandbox/](openemr-sandbox/) folder has a Docker Compose setup that runs OpenEMR with a MariaDB database. We use it only to show the OpenEMR screen in the demo.

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
