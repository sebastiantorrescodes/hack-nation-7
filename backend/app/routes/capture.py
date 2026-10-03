"""Expert capture: screen frames + voice transcript -> Work Map."""

import json
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import store
from ..llm import CLAUDE_FAST_MODEL, FRAME_SCHEMA, WORKMAP_SCHEMA, claim_fields_doc, structured
from ..models import CaptureSession, ScreenEvent, Skill, TranscriptTurn, WorkMap

router = APIRouter(prefix="/api/capture", tags=["capture"])


def _get(session_id: str) -> CaptureSession:
    s = store.load("sessions", session_id, CaptureSession)
    if not s:
        raise HTTPException(404, "capture session not found")
    return s


class StartBody(BaseModel):
    expert_name: str


@router.post("/sessions")
def start(body: StartBody) -> CaptureSession:
    s = CaptureSession(id=uuid.uuid4().hex[:12], expert_name=body.expert_name, started_at=time.time())
    store.save("sessions", s)
    return s


@router.get("/sessions")
def list_sessions() -> list[CaptureSession]:
    return store.list_all("sessions", CaptureSession)


@router.get("/sessions/{session_id}")
def get_session(session_id: str) -> CaptureSession:
    return _get(session_id)


@router.post("/sessions/{session_id}/transcript")
def add_turn(session_id: str, turn: TranscriptTurn) -> dict:
    s = _get(session_id)
    s.transcript.append(turn)
    store.save("sessions", s)
    return {"ok": True}


class FrameBody(BaseModel):
    t: float
    # What the extension's content script saw the biller do since the last frame,
    # e.g. 'changed "Modifier" from "" to "25"' or 'clicked "Save"'.
    actions: list[str] = []
    # Text snapshot of every OpenEMR frame: field labels/values plus visible text.
    page: str = ""
    image_base64: str | None = None  # optional JPEG from chrome.tabs.captureVisibleTab, no data: prefix


FRAME_SYSTEM = f"""You watch a senior medical biller work claims in OpenEMR. Each update gives you the exact
UI actions they just took, a text snapshot of the page, and sometimes a screenshot.
An interviewer (a voice agent) will ask the biller about their reasoning, so your job is to notice
*decisions* — moments where the biller exercises judgment, not routine navigation.

Decision points include: adding/removing a modifier, changing a CPT or ICD-10 code, holding a claim,
writing a physician query, overriding a payer default, choosing not to bill something, splitting a claim.
Routine actions (opening a menu, scrolling, typing demographics) are not decision points.

For each update return:
- screen_summary: what is on screen now, in one or two sentences (this becomes the "previous screen" next time)
- claim_fields_json: a JSON object string with any claim fields you can read, using only these keys:
{claim_fields_doc()}
- changed: whether something meaningful changed versus the previous screen
- event_kind / event_description: short label and description of what the biller just did ("" if nothing)
- is_decision_point: true only for a real judgment call
- ask_why: if it is a decision point, one short, natural question the interviewer should ask, referring to
  the specific thing they did (e.g. "I saw you added modifier 25 to the 99214 — what told you that was needed?").
  Empty string otherwise."""


@router.post("/sessions/{session_id}/frames")
async def analyze_frame(session_id: str, body: FrameBody) -> ScreenEvent | None:
    s = _get(session_id)
    recent = "\n".join(f"[{t.role}] {t.text}" for t in s.transcript[-6:]) or "(nothing yet)"
    actions = "\n".join(f"- {a}" for a in body.actions) or "(none recorded)"
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Previous screen: {s.last_screen_summary or '(first update)'}\n\n"
                f"Actions just taken:\n{actions}\n\n"
                f"Recent conversation:\n{recent}\n\n"
                f"Page snapshot:\n{body.page[:40000]}"
            ),
        }
    ]
    if body.image_base64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": body.image_base64}})
    result = await structured(
        model=CLAUDE_FAST_MODEL,
        effort="low",
        max_tokens=4000,
        system=FRAME_SYSTEM,
        content=content,
        schema=FRAME_SCHEMA,
    )
    # Reload: transcript turns may have been written while Claude was thinking.
    s = _get(session_id)
    s.last_screen_summary = result["screen_summary"]
    event = None
    if result["changed"] and result["event_kind"]:
        try:
            fields = json.loads(result["claim_fields_json"] or "{}")
        except json.JSONDecodeError:
            fields = {}
        event = ScreenEvent(
            t=body.t,
            kind=result["event_kind"],
            description=result["event_description"],
            claim_fields=fields,
            is_decision_point=result["is_decision_point"],
            ask_why=result["ask_why"] or None,
        )
        s.events.append(event)
    store.save("sessions", s)
    return event


WORKMAP_SYSTEM = f"""You turn a recorded working session of a senior medical biller into a Work Map:
a set of skill records that a tutor will use to train new billers and catch mistakes before claims are saved.

You get a timeline of screen events (what the biller did in OpenEMR) and the transcript of a voice interview
where they explained their reasoning. Produce one skill per distinct, reusable judgment call. Merge repeats.
Skip anything that was not explained or is pure navigation.

Each skill has:
- title: short imperative name ("Add modifier 25 when E/M and minor procedure share a date")
- trigger: conditions on claim fields that must ALL be true for this skill to apply. Use only these fields:
{claim_fields_doc()}
  Ops: eq, neq, in, not_in, contains, not_contains (lists), gt, lt (numbers), is_true, is_false, empty, not_empty.
  `values` is always a list of strings (empty for is_true/is_false/empty/not_empty). Make triggers specific
  enough that they don't fire on unrelated claims, but general enough to transfer to new claims.
  Triggers describe the claim as it ARRIVES, before the biller fixes it.
- action: what the expert did (kind + one-line detail).
- expert_explanation: the expert's reasoning in their own words — quote or lightly clean up the transcript,
  keep their voice, 1-3 sentences. Never invent reasoning they didn't give.
- guardrail: a description plus `must` conditions that must hold on the claim when it is saved, whenever the
  trigger matched. This is what catches a new hire's mistake (e.g. modifiers contains ["25"], or status eq ["held"]).
- evidence: timestamps (seconds) and short quotes from the transcript that support the skill."""


@router.post("/sessions/{session_id}/workmap")
async def build_workmap(session_id: str) -> WorkMap:
    s = _get(session_id)
    if not s.transcript and not s.events:
        raise HTTPException(400, "nothing captured yet")
    timeline = sorted(
        [{"t": e.t, "type": "screen", "kind": e.kind, "description": e.description, "claim_fields": e.claim_fields} for e in s.events]
        + [{"t": t.t, "type": "speech", "role": t.role, "text": t.text} for t in s.transcript],
        key=lambda x: x["t"],
    )
    result = await structured(
        effort="high",
        system=WORKMAP_SYSTEM,
        content=f"Expert: {s.expert_name}\n\nTimeline (JSON):\n{json.dumps(timeline, indent=1)}",
        schema=WORKMAP_SCHEMA,
    )
    wm = WorkMap(
        id=uuid.uuid4().hex[:12],
        session_id=s.id,
        expert_name=s.expert_name,
        summary=result["summary"],
        skills=[Skill(id=f"s{i + 1}", **sk) for i, sk in enumerate(result["skills"])],
    )
    store.save("workmaps", wm)
    s.workmap_id = wm.id
    store.save("sessions", s)
    return wm
