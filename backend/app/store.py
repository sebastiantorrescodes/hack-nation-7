"""Supabase persistence. Maps the API models (models.py) onto the tables in scripts/db.sql.

- A workflow is what gets taught. It defines the fields of the records it works on; experts record capture
  sessions into it; trainees practice its approved skills (the published Work Map) on its practice `cases`.
- A capture session is a `sessions` row (kind='capture'); its transcript turns and screen events are
  `transcript_segments` and `events` rows.
- A Work Map is the set of skills whose source_session is a capture session, so its id is that session's id
  and its summary lives on the session row. Skills dropped by a rebuild or an edit are marked 'rejected'
  instead of deleted, because attempts reference them.
- A tutor session is a `sessions` row (kind='training'); attempts and tutor messages are `attempts` and
  `interventions` rows.
"""

import json
import uuid
from datetime import datetime, timezone

from supabase import AsyncClient, acreate_client

from .access import authorize_owner, caller
from .knowledge import draft_payload
from .config import SEED_DIR, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from .models import (
    Action,
    Attempt,
    CaptureSession,
    EvidenceRef,
    Guardrail,
    PracticeCase,
    RecordField,
    ScreenEvent,
    Skill,
    TranscriptTurn,
    TutorSession,
    Workflow,
    WorkMap,
)

# Fixed id of the seeded demo capture session, so seeding runs once.
DEMO_ID = "00000000-0000-4000-8000-00000000de01"

_db: AsyncClient | None = None


async def init() -> None:
    global _db
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in backend/.env")
    _db = await acreate_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    await _seed_demo()


def _t(name: str):
    assert _db, "store.init() was not called"
    return _db.table(name)


def _id(id: str) -> str | None:
    """Normalizes an id from a URL. None if it isn't a uuid, so lookups 404 instead of erroring in Postgres."""
    try:
        return str(uuid.UUID(id))
    except ValueError:
        return None


def _ms(t: float) -> int:
    return round(t * 1000)


def _epoch(ts: str) -> float:
    return datetime.fromisoformat(ts).timestamp()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _name(row: dict) -> str:
    return (row.get("users") or {}).get("name", "")


async def _user_id(name: str, role: str) -> str:
    if not caller.get().local:
        return caller.get().user_id
    rows = (await _t("users").select("id").eq("name", name).eq("role", role).limit(1).execute()).data
    if rows:
        return rows[0]["id"]
    return (await _t("users").insert({"name": name, "role": role}).execute()).data[0]["id"]


# --- Workflows --------------------------------------------------------------
_WORKFLOW_COLS = "*, skills(id, status), sessions(kind, summary)"


def _workflow(row: dict) -> Workflow:
    return Workflow(
        id=row["id"],
        name=row["name"],
        app=row["app"],
        description=row["description"] or "",
        fields=row["fields"] or [],
        approved_skills=sum(s["status"] == "approved" for s in row["skills"]),
        draft_skills=sum(s["status"] == "draft" for s in row["skills"]),
        sessions=sum(s["kind"] == "capture" and s["summary"] is not None for s in row["sessions"]),
    )


async def list_workflows() -> list[Workflow]:
    rows = (await _t("workflows").select(_WORKFLOW_COLS).order("created_at").execute()).data
    return [_workflow(r) for r in rows]


async def get_workflow(workflow_id: str) -> Workflow | None:
    wid = _id(workflow_id)
    if not wid:
        return None
    rows = (await _t("workflows").select(_WORKFLOW_COLS).eq("id", wid).execute()).data
    return _workflow(rows[0]) if rows else None


async def create_workflow(name: str, app: str, description: str = "", fields: list[RecordField] | None = None) -> Workflow:
    row = (
        await _t("workflows")
        .insert({"name": name, "app": app, "description": description, "fields": [f.model_dump() for f in fields or []]})
        .execute()
    ).data[0]
    return _workflow({**row, "skills": [], "sessions": []})


async def update_workflow(
    workflow_id: str,
    *,
    name: str | None = None,
    app: str | None = None,
    description: str | None = None,
    fields: list[RecordField] | None = None,
) -> Workflow | None:
    changes: dict = {k: v for k, v in {"name": name, "app": app, "description": description}.items() if v is not None}
    if fields is not None:
        changes["fields"] = [f.model_dump() for f in fields]
    wid = _id(workflow_id)
    if wid and changes:
        await _db.rpc("update_workflow_atomic", {"p_workflow": wid, "p_changes": changes}).execute()
    return await get_workflow(workflow_id)


async def list_published_workflows(learner_name: str | None) -> list[Workflow]:
    """Workflows with at least one approved skill, with the learner's mastered-skill count."""
    workflows = [w for w in await list_workflows() if w.approved_skills]
    if learner_name:
        mastered: set[str] = set()
        users = [{"id": caller.get().user_id}] if not caller.get().local else (await _t("users").select("id").eq("name", learner_name).eq("role", "trainee").execute()).data
        if users:
            rows = (await _t("mastery").select("skill_id").eq("trainee_id", users[0]["id"]).eq("level", "mastered").execute()).data
            mastered = {r["skill_id"] for r in rows}
        approved = (await _t("skills").select("id, workflow_id").eq("status", "approved").execute()).data
        for w in workflows:
            w.mastered_skills = sum(s["id"] in mastered for s in approved if s["workflow_id"] == w.id)
    return workflows


# --- Practice cases ---------------------------------------------------------
def _case(row: dict) -> PracticeCase:
    return PracticeCase(id=row["id"], workflow_id=row["workflow_id"], label=row["label"], data=row["data"])


async def list_cases(workflow_id: str) -> list[PracticeCase]:
    rows = (await _t("cases").select("*").eq("workflow_id", workflow_id).order("created_at").execute()).data
    return [_case(r) for r in rows]


async def get_case(case_id: str) -> PracticeCase | None:
    cid = _id(case_id)
    rows = (await _t("cases").select("*").eq("id", cid).execute()).data if cid else []
    return _case(rows[0]) if rows else None


async def create_case(workflow_id: str, label: str, data: dict) -> PracticeCase:
    row = (await _t("cases").insert({"workflow_id": workflow_id, "label": label, "data": data}).execute()).data[0]
    return _case(row)


async def delete_case(case_id: str) -> bool:
    cid = _id(case_id)
    return bool(cid and (await _t("cases").delete().eq("id", cid).execute()).data)


# --- Capture ----------------------------------------------------------------
_CAPTURE_COLS = "*, users!sessions_user_id_fkey(name), transcript_segments(*), events(*)"


def _capture(row: dict) -> CaptureSession:
    segments = sorted((r for r in row["transcript_segments"] if not r["off_record"]), key=lambda r: (r["t_start_ms"] or 0, r["id"]))
    events = sorted(row["events"], key=lambda r: (r["t_offset_ms"] or 0, r["id"]))
    return CaptureSession(
        id=row["id"],
        workflow_id=row["workflow_id"],
        expert_name=_name(row),
        started_at=_epoch(row["started_at"]),
        transcript=[TranscriptTurn(client_id=r.get("client_id"), segment_id=r["id"], question_id=r.get("question_id"), answer_id=r.get("answer_id"), t=(r["t_start_ms"] or 0) / 1000, role=r["speaker"], text=r["text"]) for r in segments],
        events=[ScreenEvent(event_id=r["id"], client_event_id=r.get("client_id"), t=(r["t_offset_ms"] or 0) / 1000, kind=r["type"],
            observed_action=(r["payload"] or {}).get("normalized_event"),
            **{k: v for k, v in (r["payload"] or {}).items()
               if k in {"description", "record_fields", "is_decision_point", "ask_why", "question_id"}}) for r in events],
        last_screen_summary=row["screen_summary"] or "",
        workmap_id=row["id"] if row["summary"] is not None else None,
        phase=row.get("capture_phase", "capture"), workmap_revision=row.get("workmap_revision", 0),
    )


async def create_capture_session(expert_name: str, workflow_id: str, session_id: str | None = None) -> CaptureSession:
    if session_id:
        existing = await get_capture_session(session_id)
        if existing:
            if existing.workflow_id != workflow_id or existing.expert_name != expert_name:
                raise ValueError("Session identity was reused.")
            return existing
    row = (
        await _t("sessions")
        .insert({**({"id": session_id} if session_id else {}), "owner_principal": caller.get().auth_id, "kind": "capture", "user_id": await _user_id(expert_name, "expert"), "workflow_id": workflow_id})
        .execute()
    ).data[0]
    return CaptureSession(id=row["id"], workflow_id=workflow_id, expert_name=expert_name, started_at=_epoch(row["started_at"]))


async def get_capture_session(session_id: str) -> CaptureSession | None:
    sid = _id(session_id)
    if not sid:
        return None
    rows = (await _t("sessions").select(_CAPTURE_COLS).eq("id", sid).eq("kind", "capture").execute()).data
    if rows: authorize_owner(rows[0])
    return _capture(rows[0]) if rows else None


async def list_capture_sessions() -> list[CaptureSession]:
    query = _t("sessions").select(_CAPTURE_COLS).eq("kind", "capture")
    if not caller.get().local and caller.get().role != "admin":
        query = query.eq("owner_principal", caller.get().auth_id)
    rows = (await query.order("started_at").execute()).data
    return [_capture(r) for r in rows]


async def add_turn(session_id: str, turn: TranscriptTurn) -> int:
    return (await _db.rpc("ingest_transcript", {"p_session": session_id,
        "p_client": turn.client_id or uuid.uuid4().hex, "p_turn": turn.model_dump(mode="json")}).execute()).data


async def ingest_capture(session_id: str, client_id: str, payload: dict) -> dict:
    from .events.normalizer import normalize_browser_event
    if any(not event.get("event_id") for event in payload.get("events", [])):
        raise ValueError("Every browser event needs a stable identity.")
    payload = {**payload, "events": [normalize_browser_event(e).model_dump(mode="json") for e in payload.get("events", [])]}
    return (await _db.rpc("ingest_capture", {"p_session": session_id, "p_client": client_id, "p_payload": payload}).execute()).data


async def complete_batch(session_id: str, client_id: str, result: ScreenEvent | None, summary: str) -> None:
    await _db.rpc("complete_capture_batch", {"p_session": session_id, "p_client": client_id,
        "p_result": result.model_dump(mode="json") if result else None, "p_summary": summary}).execute()


async def capture_evidence(session_id: str) -> tuple[list[dict], list[dict]]:
    segments = await _segments(session_id)
    events = (await _t("events").select("id,payload").eq("session_id", session_id).execute()).data
    return segments, events


async def set_capture_phase(session_id: str, phase: str) -> None:
    await _t("sessions").update({"capture_phase": phase}).eq("id", session_id).execute()


async def record_frame(session_id: str, screen_summary: str, event: ScreenEvent | None) -> None:
    await _t("sessions").update({"screen_summary": screen_summary}).eq("id", session_id).execute()
    if event:
        payload = event.model_dump(include={"description", "record_fields", "is_decision_point", "ask_why"})
        await _t("events").insert(
            {"session_id": session_id, "t_offset_ms": _ms(event.t), "type": event.kind, "payload": payload}
        ).execute()


# --- Work Maps and skills ---------------------------------------------------
_EVIDENCE_COLS = "skill_evidence(quote, segment_id, event_id, transcript_segments(t_start_ms))"
_WORKMAP_COLS = (
    "id, workflow_id, summary, started_at, owner_principal, workmap_revision, capture_phase, teach_back_revision, users!sessions_user_id_fkey(name), "
    f"skills!skills_source_session_fkey(*, {_EVIDENCE_COLS})"
)
# A skill on its own, with the name of the expert whose session it came from.
_SKILL_COLS = f"*, {_EVIDENCE_COLS}, sessions!skills_source_session_fkey(users!sessions_user_id_fkey(name))"


def _skill(row: dict, expert_name: str | None = None) -> Skill:
    if expert_name is None:
        expert_name = _name(row.get("sessions") or {})
    evidence = [
        EvidenceRef(segment_id=e.get("segment_id"), event_id=e.get("event_id"), t=((e["transcript_segments"] or {}).get("t_start_ms") or 0) / 1000, quote=e["quote"] or "")
        for e in row["skill_evidence"]
    ]
    return Skill(
        id=row["id"], version=row.get("version", 1), supersedes=row.get("supersedes"),
        title=row["name"],
        trigger=(row["trigger"] or {}).get("all", []),
        action=Action(kind=row["action_kind"] or "other", detail=row["action"]),
        expert_explanation=row["reason_quote"] or "",
        guardrail=Guardrail(description=row["guardrail_msg"] or "", must=(row["guardrail"] or {}).get("all", [])),
        evidence=sorted(evidence, key=lambda e: e.t),
        status=row["status"],
        expert_name=expert_name,
    )


def _workmap(row: dict) -> WorkMap:
    skills = sorted((s for s in row["skills"] if s["status"] != "rejected"), key=lambda s: s["created_at"])
    return WorkMap(
        id=row["id"],
        session_id=row["id"],
        workflow_id=row["workflow_id"],
        expert_name=_name(row),
        summary=row["summary"] or "",
        recorded_at=_epoch(row["started_at"]),
        skills=[_skill(s, _name(row)) for s in skills],
        revision=row.get("workmap_revision", 0),
        teach_back_confirmed=row.get("capture_phase") == "finished" and row.get("teach_back_revision") == row.get("workmap_revision"),
    )


def _skill_row(sk: Skill) -> dict:
    return {
        "name": sk.title,
        "trigger": {"all": [c.model_dump() for c in sk.trigger]},
        "action": sk.action.detail,
        "action_kind": sk.action.kind,
        "reason_quote": sk.expert_explanation,
        "guardrail": {"all": [c.model_dump() for c in sk.guardrail.must]},
        "guardrail_msg": sk.guardrail.description,
    }


def _segment_for(e: EvidenceRef, segments: list[dict]) -> int | None:
    from .knowledge import resolve_evidence
    try:
        return resolve_evidence(e, segments, [{"id": e.event_id}]).segment_id
    except ValueError:
        return None


async def _segments(session_id: str) -> list[dict]:
    return (await _t("transcript_segments").select("id, t_start_ms, text, speaker, off_record").eq("session_id", session_id).execute()).data


async def _set_evidence(skill_id: str, evidence: list[EvidenceRef], segments: list[dict]) -> None:
    await _t("skill_evidence").delete().eq("skill_id", skill_id).execute()
    if evidence:
        await _t("skill_evidence").insert(
            [{"skill_id": skill_id, "segment_id": _segment_for(e, segments), "quote": e.quote} for e in evidence]
        ).execute()


async def _insert_skill(session_id: str, workflow_id: str, sk: Skill, segments: list[dict], status: str) -> None:
    # One insert per skill (not a batch) so created_at keeps the Work Map's order.
    row = (
        await _t("skills")
        .insert({**_skill_row(sk), "workflow_id": workflow_id, "source_session": session_id, "status": status})
        .execute()
    ).data[0]
    await _set_evidence(row["id"], sk.evidence, segments)


async def get_workmap(workmap_id: str) -> WorkMap | None:
    sid = _id(workmap_id)
    if not sid:
        return None
    rows = (await _t("sessions").select(_WORKMAP_COLS).eq("id", sid).not_.is_("summary", "null").execute()).data
    if rows: authorize_owner(rows[0])
    return _workmap(rows[0]) if rows else None


async def list_workmaps(workflow_id: str | None = None) -> list[WorkMap]:
    q = _t("sessions").select(_WORKMAP_COLS).eq("kind", "capture").not_.is_("summary", "null")
    if workflow_id:
        q = q.eq("workflow_id", workflow_id)
    if not caller.get().local and caller.get().role != "admin":
        q = q.eq("owner_principal", caller.get().auth_id)
    rows = (await q.order("started_at", desc=True).execute()).data
    return [_workmap(r) for r in rows]


async def create_workmap(session_id: str, summary: str, skills: list[Skill], *,
                         fields: list[RecordField] | None = None, client_id: str | None = None,
                         expected_revision: int | None = None, edit: bool = False) -> WorkMap:
    session = await get_capture_session(session_id)
    workflow = await get_workflow(session.workflow_id)
    segments, events = await capture_evidence(session_id)
    fields = fields if fields is not None else workflow.fields
    payload = {"summary": summary, "skills": draft_payload(skills, fields, segments, events),
               "fields": [f.model_dump() for f in fields], "mode": "edit" if edit else "build"}
    await _db.rpc("build_workmap_atomic", {"p_session": session_id, "p_client": client_id or uuid.uuid4().hex,
        "p_expected": session.workmap_revision if expected_revision is None else expected_revision,
        "p_payload": payload}).execute()
    return await get_workmap(session_id)


async def update_workmap(wm: WorkMap) -> WorkMap | None:
    existing = await get_workmap(wm.id)
    if not existing: return None
    current = {s.id: s for s in existing.skills}
    updated = []
    for skill in wm.skills:
        if skill.id and skill.id not in current:
            raise ValueError("Skill does not belong to this Work Map.")
        if skill.id:
            old = current[skill.id]
            if skill.version != old.version:
                raise ValueError("The skill version changed. Refresh before editing.")
            skill = skill.model_copy(update={"supersedes": old.id, "status": "draft"})
        updated.append(skill)
    return await create_workmap(wm.id, wm.summary, updated, expected_revision=wm.revision, edit=True)


async def get_skill(skill_id: str) -> Skill | None:
    sid = _id(skill_id)
    if not sid:
        return None
    rows = (await _t("skills").select(_SKILL_COLS).eq("id", sid).execute()).data
    if rows:
        session_rows = (await _t("sessions").select("owner_principal").eq("id", rows[0]["source_session"]).execute()).data
        if session_rows: authorize_owner(session_rows[0])
    return _skill(rows[0]) if rows else None


async def set_skill_status(skill_id: str, status: str, expected_version: int) -> Skill | None:
    sk = await get_skill(skill_id)
    if not sk: return None
    row = (await _t("skills").select("source_session").eq("id", skill_id).execute()).data[0]
    await get_capture_session(row["source_session"])
    actor = caller.get().user_id or await _user_id("Local operator", "admin")
    await _db.rpc("review_skill_atomic", {"p_skill": skill_id, "p_expected": expected_version,
        "p_status": status, "p_actor": actor}).execute()
    return await get_skill(skill_id)


async def published_skills(workflow_id: str) -> list[Skill]:
    """The published Work Map: every approved skill in the workflow, from any expert session."""
    rows = (
        await _t("skills").select(_SKILL_COLS).eq("workflow_id", workflow_id).eq("status", "approved").order("created_at").execute()
    ).data
    return [_skill(r) for r in rows]


async def _seed_demo() -> None:
    """Seeds a published sample workflow, with practice cases, so the tutor can be tried before anything is captured."""
    if (await _t("sessions").select("id").eq("id", DEMO_ID).execute()).data:
        return
    demo = json.loads((SEED_DIR / "demo_workflow.json").read_text(encoding="utf-8"))
    wf = await create_workflow(**{**demo["workflow"], "fields": [RecordField(**f) for f in demo["workflow"]["fields"]]})
    await _t("sessions").insert(
        {
            "id": DEMO_ID,
            "kind": "capture",
            "status": "done",
            "user_id": await _user_id(demo["expert_name"], "expert"),
            "workflow_id": wf.id,
            "summary": demo["summary"],
            "ended_at": _now(),
        }
    ).execute()
    for sk in demo["skills"]:
        await _insert_skill(DEMO_ID, wf.id, Skill(id="", **sk), [], "approved")
    for c in demo["cases"]:
        await create_case(wf.id, c["label"], c["data"])


# --- Tutor ------------------------------------------------------------------
_TUTOR_COLS = "*, users!sessions_user_id_fkey(name), attempts(*)"


def _attempt(r: dict) -> Attempt:
    # Predictions carry the learner's answer; save checks don't.
    if r["prediction"] is not None:
        return Attempt(skill_id=r["skill_id"], kind="prediction", correct=r["outcome"] == "correct", detail=r["prediction"])
    return Attempt(skill_id=r["skill_id"], kind="save_check", correct=r["outcome"] == "correct", detail="")


def _tutor(row: dict) -> TutorSession:
    record = row["record"] or {}
    return TutorSession(
        id=row["id"],
        learner_name=_name(row),
        workflow_id=row["workflow_id"],
        case_id=record.get("id", ""),
        original_record=record,
        matched_skill_ids=row["matched_skill_ids"] or [],
        attempts=[_attempt(a) for a in sorted(row["attempts"], key=lambda a: (a["created_at"], a.get("ordinal", 0)))],
        saved=row["status"] == "done",
        skill_snapshot=[Skill.model_validate(s) for s in row.get("skill_snapshot") or []],
        workflow_snapshot=Workflow.model_validate(row["workflow_snapshot"]) if row.get("workflow_snapshot") else None,
    )


async def create_tutor_session(learner_name: str, workflow_id: str, record: dict, matched_skill_ids: list[str], *, skills: list[Skill], workflow: Workflow) -> TutorSession:
    sid = (await _db.rpc("start_tutor_atomic", {"p_actor": await _user_id(learner_name, "trainee"),
        "p_owner": caller.get().auth_id, "p_workflow": workflow_id, "p_record": record,
        "p_matched": matched_skill_ids, "p_skills": [s.model_dump(mode="json") for s in skills],
        "p_workflow_snapshot": workflow.model_dump(mode="json")}).execute()).data
    return await get_tutor_session(sid)


async def get_tutor_session(session_id: str) -> TutorSession | None:
    sid = _id(session_id)
    if not sid:
        return None
    rows = (await _t("sessions").select(_TUTOR_COLS).eq("id", sid).eq("kind", "training").execute()).data
    if rows: authorize_owner(rows[0])
    return _tutor(rows[0]) if rows else None


async def record_prediction(ts: TutorSession, skill_id: str, prediction: str, correct: bool, feedback: str) -> None:
    await _db.rpc("record_tutor_attempt", {"p_session": ts.id, "p_kind": "prediction", "p_skill": skill_id,
        "p_prediction": prediction, "p_correct": correct, "p_feedback": feedback}).execute()


async def record_save_check(ts: TutorSession, blocked: dict[str, str]) -> None:
    await _db.rpc("record_tutor_attempt", {"p_session": ts.id, "p_kind": "save_check", "p_skill": None,
        "p_prediction": None, "p_correct": None, "p_feedback": None, "p_blocked": blocked}).execute()
