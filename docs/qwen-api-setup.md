# Qwen API setup

The staged apprentice now defaults to OpenRouter API reasoning instead of Claude.
No model download, Torch, GPU, or Anthropic key is needed for this agent.

1. Create an OpenRouter account and a key at https://openrouter.ai/keys.
2. Create `backend/.env` and add these lines, replacing the placeholder locally:

   ```dotenv
   OPENROUTER_API_KEY=your-key-here
   OPENROUTER_MODEL=qwen/qwen3.8-27b:free
   ```

   The file is ignored by Git. Keep the key on the backend; never put it in frontend code or share it in chat.
3. From the repository's `backend` directory:

   ```sh
   .venv/bin/python -m pip install -r requirements-api.txt
   .venv/bin/python -m scripts.test_agent_api
   ```

The command sends one synthetic browser event through the actual orchestrator and
prints its validated decision. It may ask a question or wait; it does not invent
an expert answer. A successful live run proves this connection, not the complete
capture, voice, persistence, or teaching flow.

The default model is listed as free at https://openrouter.ai/qwen/qwen3.8-27b:free.
Availability and account limits can change. The adapter rejects models without
the `:free` suffix and sets zero maximum provider prices. It requests JSON schema
support and stops on provider errors rather than falling back to a paid model.
Switch to another compatible free model using `OPENROUTER_MODEL`; no agent tool
changes are needed. Free capacity is not a guaranteed hackathon service.

## Files

- `backend/app/api_llm.py`: server-side HTTP adapter and free-only routing.
- `backend/app/agent/apprentice.py`: calls the adapter for one bounded decision.
- `backend/scripts/test_agent_api.py`: live smoke test using synthetic facts.
- `backend/tests/test_api_llm.py`: offline API contract and failure tests.
- `backend/requirements-api.txt`: lightweight API agent dependencies.

## Remaining integration

The existing FastAPI capture/tutor routes still use the legacy Claude adapter.
The new agent must next be wired into browser capture, followed by ElevenLabs
speech and Supabase persistence. Local Qwen screenshot vision remains a separate
provider and is not enabled by this API change.

For judges, the hosted backend should hold the team's API key so visitors can
use the demo link. A local clone needs its own backend `.env`, or an explicitly
configured connection to that hosted backend. Hosting and that connection have
not been implemented yet.
