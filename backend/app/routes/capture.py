"""Expert capture: screen frames + voice transcript -> Work Map."""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import store
from ..llm import FRAME_SCHEMA, WORKMAP_SCHEMA, structured, workflow_doc
from ..models import CaptureSession, Condition, RecordField, ScreenEvent, Skill, TranscriptTurn, Workflow, WorkMap

router = APIRouter(prefix="/api/capture", tags=["capture"])


async def _get(session_id: str) -> CaptureSession:
    s = await store.get_capture_session(session_id)
    if not s:
        raise HTTPException(404, "capture session not found")
    return s


async def _workflow(s: CaptureSession) -> Workflow:
    wf = await store.get_workflow(s.workflow_id)
    if not wf:
        raise HTTPException(404, "workflow not found")
    return wf


class StartBody(BaseModel):
    expert_name: str
    workflow_id: str


@router.post("/sessions")
async def start(body: StartBody) -> CaptureSession:
    wf = await store.get_workflow(body.workflow_id)
    if not wf:
        raise HTTPException(404, "workflow not found")
    return await store.create_capture_session(body.expert_name, wf.id)


@router.get("/sessions")
async def list_sessions() -> list[CaptureSession]:
    return await store.list_capture_sessions()


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> CaptureSession:
    return await _get(session_id)


@router.post("/sessions/{session_id}/transcript")
async def add_turn(session_id: str, turn: TranscriptTurn) -> dict:
    s = await _get(session_id)
    await store.add_turn(s.id, turn)
    return {"ok": True}


class FrameBody(BaseModel):
    t: float
    # What the extension's content script saw the expert do since the last frame,
    # e.g. 'changed "Amount" to "120"' or 'clicked "Save"'.
    actions: list[str] = []
    # Text snapshot of every frame in the tab: field labels/values plus visible text.
    page: str = ""
    image_base64: str | None = None  # optional JPEG from chrome.tabs.captureVisibleTab, no data: prefix


FRAME_SYSTEM = """You watch an experienced professional do their work in a piece of software. Each update gives
you the exact UI actions they just took, a text snapshot of the page, and sometimes a screenshot.
An interviewer (a voice agent) will ask the expert about their reasoning, so your job is to notice
*decisions* — moments where the expert exercises judgment, not routine navigation.

Decision points are choices a newcomer could get wrong: changing a value the software filled in, putting
something on hold, escalating or asking someone, rejecting or splitting an item, overriding a default,
choosing not to do something they could have done. Routine actions (opening a menu, scrolling, typing in
data that is simply copied from elsewhere) are not decision points.

For each update return:
- screen_summary: what is on screen now, in one or two sentences (this becomes the "previous screen" next time)
- record_fields_json: a JSON object string with the values of the record being worked on that you can read.
  Use the workflow's record fields when they apply. For other values that seem to matter to the decision,
  add short snake_case keys. Lists are JSON arrays of strings.
- changed: whether something meaningful changed versus the previous screen
- event_kind / event_description: short snake_case label and one-line description of what the expert just did ("" if nothing)
- is_decision_point: true only for a real judgment call
- ask_why: if it is a decision point, one short, natural question the interviewer should ask, referring to the
  specific thing they did (e.g. "I saw you put that one on hold. What told you it wasn't ready?").
  Empty string otherwise."""


@router.post("/sessions/{session_id}/frames")
async def analyze_frame(session_id: str, body: FrameBody) -> ScreenEvent | None:
    s = await _get(session_id)
    wf = await _workflow(s)
    recent = "\n".join(f"[{t.role}] {t.text}" for t in s.transcript[-6:]) or "(nothing yet)"
    actions = "\n".join(f"- {a}" for a in body.actions) or "(none recorded)"
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"{workflow_doc(wf)}\n\n"
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
        effort="low",
        max_tokens=4000,
        system=FRAME_SYSTEM,
        content=content,
        schema=FRAME_SCHEMA,
    )
    event = None
    if result["changed"] and result["event_kind"]:
        try:
            fields = json.loads(result["record_fields_json"] or "{}")
        except json.JSONDecodeError:
            fields = {}
        event = ScreenEvent(
            t=body.t,
            kind=result["event_kind"],
            description=result["event_description"],
            record_fields=fields if isinstance(fields, dict) else {},
            is_decision_point=result["is_decision_point"],
            ask_why=result["ask_why"] or None,
        )
    await store.record_frame(s.id, result["screen_summary"], event)
    return event


WORKMAP_SYSTEM = """You turn a recorded working session of an expert into a Work Map: a set of skill records that a
tutor will use to train newcomers and catch their mistakes before they save.

You get the workflow (what the work is, the software it's done in, and the record fields defined so far), a
timeline of screen events (what the expert did, with the record values seen on screen) and the transcript of
a voice interview where they explained their reasoning. Produce one skill per distinct, reusable judgment
call. Merge repeats. Skip anything that was not explained or is pure navigation.

First, fields: the complete list of record fields that skills are written against. Keep every field already
defined, unchanged. Add a field only when a skill needs one that doesn't exist: a short snake_case name, a
type (string, number, boolean, or list for multi-valued things like tags or codes) and a one-line description.

Each skill has:
- title: short imperative name ("Hold expense reports over $75 that have no receipt")
- trigger: conditions on record fields that must ALL be true for this skill to apply. Only use fields from
  your fields list.
  Ops: eq, neq, in, not_in, contains, not_contains (lists), gt, lt (numbers), is_true, is_false, empty, not_empty.
  `values` is always a list of strings (empty for is_true/is_false/empty/not_empty). Make triggers specific
  enough that they don't fire on unrelated records, but general enough to transfer to new ones.
  Triggers describe the record as it ARRIVES, before the expert changes it.
- action: what the expert did (kind + one-line detail).
- expert_explanation: the expert's reasoning in their own words — quote or lightly clean up the transcript,
  keep their voice, 1-3 sentences. Never invent reasoning they didn't give.
- guardrail: a description plus `must` conditions that must hold on the record when it is saved, whenever the
  trigger matched. This is what catches a newcomer's mistake (e.g. tags contains ["urgent"], or status eq ["on_hold"]).
- evidence: timestamps (seconds) and short quotes from the transcript that support the skill."""


def _type_for(c: Condition) -> str:
    if c.op in ("is_true", "is_false"):
        return "boolean"
    if c.op in ("gt", "lt"):
        return "number"
    if c.op in ("contains", "not_contains"):
        return "list"
    return "string"


def merge_fields(existing: list[RecordField], proposed: list[RecordField], skills: list[Skill]) -> list[RecordField]:
    """The workflow's fields after a Work Map build. Existing fields (possibly edited by the expert) never change;
    new ones are added, including any a skill uses that the model forgot to declare (typed from how it's used)."""
    merged = {f.name: f for f in existing}
    for f in proposed:
        merged.setdefault(f.name, f)
    for sk in skills:
        for c in sk.trigger + sk.guardrail.must:
            merged.setdefault(c.field, RecordField(name=c.field, type=_type_for(c)))
    return list(merged.values())


@router.post("/sessions/{session_id}/workmap")
async def build_workmap(session_id: str) -> WorkMap:
    s = await _get(session_id)
    wf = await _workflow(s)
    if not s.transcript and not s.events:
        raise HTTPException(400, "nothing captured yet")
    timeline = sorted(
        [{"t": e.t, "type": "screen", "kind": e.kind, "description": e.description, "record": e.record_fields} for e in s.events]
        + [{"t": t.t, "type": "speech", "role": t.role, "text": t.text} for t in s.transcript],
        key=lambda x: x["t"],
    )
    result = await structured(
        effort="high",
        system=WORKMAP_SYSTEM,
        content=f"{workflow_doc(wf)}\n\nExpert: {s.expert_name}\n\nTimeline (JSON):\n{json.dumps(timeline, indent=1)}",
        schema=WORKMAP_SCHEMA,
    )
    # Skill ids are assigned by the database.
    skills = [Skill(id="", **sk) for sk in result["skills"]]
    fields = merge_fields(wf.fields, [RecordField(**f) for f in result["fields"]], skills)
    if fields != wf.fields:
        await store.update_workflow(wf.id, fields=fields)
    return await store.create_workmap(s.id, result["summary"], skills)
