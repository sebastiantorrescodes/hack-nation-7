# Qwen and ElevenLabs voice demo

From `backend`, run:

```sh
.venv/bin/python -m pip install -r requirements-api.txt
.venv/bin/python -m uvicorn app.voice_demo:app --host 127.0.0.1 --port 8001
```

Open http://127.0.0.1:8001 in Chrome. Start the demo, speak the question, then
press Record answer. Allow microphone access and press Stop and send when done.
Recording is limited to 45 seconds. The transcript is the expert's evidence;
Qwen can ask a follow-up or propose a workflow step using its evidence IDs.
Retry reasoning continues from the saved in-memory answer if Qwen fails.

The ElevenLabs key needs Text to Speech and Speech to Text access. It uses account
credits for short speech requests. `ELEVENLABS_VOICE_ID` is optional; the default
is the voice from ElevenLabs' API example. This direct speech flow does not use
`ELEVENLABS_AGENT_ID` or the dashboard conversational model.

## Files and responsibilities

- `backend/app/speech.py`: converts question text to audio and expert audio to text.
- `backend/app/voice_demo.py`: ties pending questions and real audio answers to the orchestrator.
- `backend/app/voice_demo.html`: explicit microphone controls, playback, transcript and proposals.
- `backend/tests/test_speech.py`: speech transport and evidence integration tests.
- `backend/scripts/test_voice_flow.py`: live synthetic audio roundtrip (uses speech credits).

This is a local stage-four demo. It uses a synthetic browser event and in-memory
sessions, lost on restart; do not run multiple server workers. Expert review,
debrief UI, real browser event capture, Supabase persistence and a hosted judge
link remain separate integrations. There is no automatic approval or publishing.
This server binds to localhost and has no public authentication layer; it is not
the public hackathon deployment yet.

Provider references: [speech generation](https://elevenlabs.io/docs/api-reference/text-to-speech/convert)
and [transcription](https://elevenlabs.io/docs/api-reference/speech-to-text/convert).
