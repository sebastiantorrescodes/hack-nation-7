"""Live synthetic audio roundtrip; never substitute this fixture for a real expert."""

import asyncio
from pathlib import Path

from app.agent.apprentice import process_event
from app.agent.schemas import CaptureContext, WorkMapState
from app.agent.tools import record_expert_answer, pending_question
from app.events.normalizer import normalize_browser_event
from app.speech import ElevenLabsSpeech, SpeechError
from app.api_llm import ReasoningAPIError


async def main():
    state = WorkMapState(expert_name="Synthetic test fixture")
    event = normalize_browser_event({"event_id":"synthetic-voice-event", "event_type":"field_change",
        "target":"Review status", "old_value":"Pending", "new_value":"Needs review", "page":"Synthetic demo"})
    context = CaptureContext(expert_paused=True)
    try:
        decision = await process_event(event,state,context)
        print(f"Qwen initial decision: {decision.action}")
        question = pending_question(state)
        if question is None:
            print("No question requested; voice roundtrip was not run.")
            return 1
        speech = ElevenLabsSpeech()
        audio = await speech.speak(question.text)
        path = Path(__file__).resolve().parents[1] / "data" / "voice-test-question.mp3"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(audio)
        print(f"Question speech generated: {len(audio)} bytes; saved in backend/data/voice-test-question.mp3.")
        fixture = "I set it to needs review when information is missing. I ask a supervisor before approving it."
        answer_audio = await speech.speak(fixture)
        transcript = await speech.transcribe(answer_audio,"audio/mpeg")
        print(f"Synthetic answer transcribed: {transcript}")
        evidence = record_expert_answer(state,question.id,transcript)
        decision = await process_event(event,state,context)
        print(f"Qwen follow-up decision: {decision.action}")
        print(f"Evidence linked correctly: {evidence.question_id == question.id and evidence.event_id == event.event_id}")
        print(f"Proposed steps: {len(state.steps)}; confirmed steps: {sum(s.status == 'confirmed' for s in state.steps)}")
        return 0
    except (SpeechError, ReasoningAPIError) as error:
        print(str(error))
        return 1
    except ValueError:
        print("Agent rejected the model's decision.")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
