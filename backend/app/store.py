"""Supabase persistence. Maps the API models (models.py) onto the tables in scripts/db.sql.

- A workflow is what gets taught. Experts record capture sessions into it; trainees practice its approved
  skills (the published Work Map).
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

from .config import SEED_DIR, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_URL
from .models import (
    Action,
    Attempt,
    CaptureSession,
    EvidenceRef,
    Guardrail,
    ScreenEvent,
    Skill,
    TranscriptTurn,
    TutorSession,
    Workflow,
    WorkMap,
)

DEMO_WORKFLOW = "Outpatient E/M billing in OpenEMR"
# Fixed id of the seeded demo capture session, so seeding runs once.
DEMO_ID = "00000000-0000-4000-8000-00000000de00"

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


async def create_workflow(name: str, app: str) -> Workflow:
    row = (await _t("workflows").insert({"name": name, "app": app}).execute()).data[0]
    return Workflow(id=row["id"], name=row["name"], app=row["app"])


async def list_published_workflows(learner_name: str | None) -> list[Workflow]:
    """Workflows with at least one approved skill, with the learner's mastered-skill count."""
    workflows = [w for w in await list_workflows() if w.approved_skills]
    if learner_name:
        mastered: set[str] = set()
        users = (await _t("users").select("id").eq("name", learner_name).eq("role", "trainee").execute()).data
        if users:
            rows = (await _t("mastery").select("skill_id").eq("trainee_id", users[0]["id"]).eq("level", "mastered").execute()).data
            mastered = {r["skill_id"] for r in rows}
        approved = (await _t("skills").select("id, workflow_id").eq("status", "approved").execute()).data
        for w in workflows:
            w.mastered_skills = sum(s["id"] in mastered for s in approved if s["workflow_id"] == w.id)
    return workflows


async def _default_workflow() -> str:
    rows = (await _t("workflows").select("id").eq("name", DEMO_WORKFLOW).limit(1).execute()).data
    return rows[0]["id"] if rows else (await _t("workflows").insert({"name": DEMO_WORKFLOW}).execute()).data[0]["id"]


# --- Capture ----------------------------------------------------------------
_CAPTURE_COLS = "*, users!sessions_user_id_fkey(name), transcript_segments(*), events(*)"


def _capture(row: dict) -> CaptureSession:
    segments = sorted((r for r in row["transcript_segments"] if not r["off_record"]), key=lambda r: (r["t_start_ms"] or 0, r["id"]))
    events = sorted(row["events"], key=lambda r: (r["t_offset_ms"] or 0, r["id"]))
    return CaptureSession(
        id=row["id"],
        expert_name=_name(row),
        started_at=_epoch(row["started_at"]),
        transcript=[TranscriptTurn(t=(r["t_start_ms"] or 0) / 1000, role=r["speaker"], text=r["text"]) for r in segments],
        events=[ScreenEvent(t=(r["t_offset_ms"] or 0) / 1000, kind=r["type"], **r["payload"]) for r in events],
        last_screen_summary=row["screen_summary"] or "",
        workmap_id=row["id"] if row["summary"] is not None else None,
    )


async def create_capture_session(expert_name: str, workflow_id: str) -> CaptureSession:
    row = (
        await _t("sessions")
        .insert({"kind": "capture", "user_id": await _user_id(expert_name, "expert"), "workflow_id": workflow_id})
        .execute()
    ).data[0]
    return CaptureSession(id=row["id"], expert_name=expert_name, started_at=_epoch(row["started_at"]))


async def get_capture_session(session_id: str) -> CaptureSession | None:
    sid = _id(session_id)
    if not sid:
        return None
    rows = (await _t("sessions").select(_CAPTURE_COLS).eq("id", sid).eq("kind", "capture").execute()).data
    return _capture(rows[0]) if rows else None


async def list_capture_sessions() -> list[CaptureSession]:
    rows = (await _t("sessions").select(_CAPTURE_COLS).eq("kind", "capture").order("started_at").execute()).data
    return [_capture(r) for r in rows]


async def add_turn(session_id: str, turn: TranscriptTurn) -> None:
    await _t("transcript_segments").insert(
        {"session_id": session_id, "speaker": turn.role, "t_start_ms": _ms(turn.t), "text": turn.text}
    ).execute()


async def record_frame(session_id: str, screen_summary: str, event: ScreenEvent | None) -> None:
    await _t("sessions").update({"screen_summary": screen_summary}).eq("id", session_id).execute()
    if event:
        payload = event.model_dump(include={"description", "claim_fields", "is_decision_point", "ask_why"})
        await _t("events").insert(
            {"session_id": session_id, "t_offset_ms": _ms(event.t), "type": event.kind, "payload": payload}
        ).execute()


# --- Work Maps and skills ---------------------------------------------------
_EVIDENCE_COLS = "skill_evidence(quote, transcript_segments(t_start_ms))"
_WORKMAP_COLS = (
    "id, workflow_id, summary, started_at, users!sessions_user_id_fkey(name), "
    f"skills!skills_source_session_fkey(*, {_EVIDENCE_COLS})"
)
# A skill on its own, with the name of the expert whose session it came from.
_SKILL_COLS = f"*, {_EVIDENCE_COLS}, sessions!skills_source_session_fkey(users!sessions_user_id_fkey(name))"


def _skill(row: dict, expert_name: str | None = None) -> Skill:
    if expert_name is None:
        expert_name = _name(row.get("sessions") or {})
    evidence = [
        EvidenceRef(t=((e["transcript_segments"] or {}).get("t_start_ms") or 0) / 1000, quote=e["quote"] or "")
        for e in row["skill_evidence"]
    ]
    return Skill(
        id=row["id"],
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
    """The transcript segment a piece of evidence came from: one containing the quote, else the nearest in time."""
    if not segments:
        return None
    q = e.quote.strip().lower()
    hits = [s for s in segments if q and q in s["text"].lower()] or segments
    return min(hits, key=lambda s: abs((s["t_start_ms"] or 0) - _ms(e.t)))["id"]


async def _segments(session_id: str) -> list[dict]:
    return (await _t("transcript_segments").select("id, t_start_ms, text").eq("session_id", session_id).execute()).data


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
    return _workmap(rows[0]) if rows else None


async def list_workmaps(workflow_id: str | None = None) -> list[WorkMap]:
    q = _t("sessions").select(_WORKMAP_COLS).eq("kind", "capture").not_.is_("summary", "null")
    if workflow_id:
        q = q.eq("workflow_id", workflow_id)
    rows = (await q.order("started_at", desc=True).execute()).data
    return [_workmap(r) for r in rows]


async def create_workmap(session_id: str, summary: str, skills: list[Skill]) -> WorkMap:
    """Stores the Work Map built from a capture session as draft skills. Rebuilding replaces the previous skills."""
    await _t("skills").update({"status": "rejected"}).eq("source_session", session_id).neq("status", "rejected").execute()
    workflow_id = (await _t("sessions").select("workflow_id").eq("id", session_id).execute()).data[0]["workflow_id"]
    segments = await _segments(session_id)
    for sk in skills:
        await _insert_skill(session_id, workflow_id, sk, segments, "draft")
    await _t("sessions").update({"summary": summary, "status": "done", "ended_at": _now()}).eq("id", session_id).execute()
    return await get_workmap(session_id)


async def update_workmap(wm: WorkMap) -> WorkMap | None:
    """Expert edits: update skills that exist, add new ones, reject the ones left out."""
    sid = _id(wm.id)
    existing = await get_workmap(sid) if sid else None
    if not existing:
        return None
    current = {
        r["id"]: r["version"]
        for r in (await _t("skills").select("id, version").eq("source_session", sid).neq("status", "rejected").execute()).data
    }
    segments = await _segments(sid)
    for sk in wm.skills:
        if sk.id in current:
            await _t("skills").update({**_skill_row(sk), "version": current.pop(sk.id) + 1}).eq("id", sk.id).execute()
            await _set_evidence(sk.id, sk.evidence, segments)
        else:
            await _insert_skill(sid, existing.workflow_id, sk, segments, "draft")
    if current:
        await _t("skills").update({"status": "rejected"}).in_("id", list(current)).execute()
    await _t("sessions").update({"summary": wm.summary}).eq("id", sid).execute()
    return await get_workmap(sid)


async def get_skill(skill_id: str) -> Skill | None:
    sid = _id(skill_id)
    if not sid:
        return None
    rows = (await _t("skills").select(_SKILL_COLS).eq("id", sid).execute()).data
    return _skill(rows[0]) if rows else None


async def set_skill_status(skill_id: str, status: str) -> Skill | None:
    """Expert review. Approving publishes the skill to trainees; the approver is the expert who recorded it."""
    sid = _id(skill_id)
    rows = (await _t("skills").select("source_session").eq("id", sid).execute()).data if sid else []
    if not rows:
        return None
    update: dict = {"status": status, "approved_by": None}
    if status == "approved":
        session = (await _t("sessions").select("user_id").eq("id", rows[0]["source_session"]).execute()).data
        update["approved_by"] = session[0]["user_id"] if session else None
    await _t("skills").update(update).eq("id", sid).execute()
    return await get_skill(sid)


async def published_skills(workflow_id: str) -> list[Skill]:
    """The published Work Map: every approved skill in the workflow, from any expert session."""
    rows = (
        await _t("skills").select(_SKILL_COLS).eq("workflow_id", workflow_id).eq("status", "approved").order("created_at").execute()
    ).data
    return [_skill(r) for r in rows]


async def _seed_demo() -> None:
    """Seeds a published demo workflow so the tutor can be tried before any capture session exists."""
    if (await _t("sessions").select("id").eq("id", DEMO_ID).execute()).data:
        return
    wm = WorkMap.model_validate_json((SEED_DIR / "demo_workmap.json").read_text(encoding="utf-8"))
    workflow_id = await _default_workflow()
    await _t("sessions").insert(
        {
            "id": DEMO_ID,
            "kind": "capture",
            "status": "done",
            "user_id": await _user_id(wm.expert_name, "expert"),
            "workflow_id": workflow_id,
            "summary": wm.summary,
            "ended_at": _now(),
        }
    ).execute()
    for sk in wm.skills:
        await _insert_skill(DEMO_ID, workflow_id, sk, [], "approved")


# --- Tutor ------------------------------------------------------------------
_TUTOR_COLS = "*, users!sessions_user_id_fkey(name), attempts(*)"


def _attempt(r: dict) -> Attempt:
    # Predictions carry the learner's answer; save checks don't.
    if r["prediction"] is not None:
        return Attempt(skill_id=r["skill_id"], kind="prediction", correct=r["outcome"] == "correct", detail=r["prediction"])
    return Attempt(skill_id=r["skill_id"], kind="save_check", correct=r["outcome"] == "correct", detail="")


def _tutor(row: dict) -> TutorSession:
    claim = row["claim"] or {}
    return TutorSession(
        id=row["id"],
        learner_name=_name(row),
        workflow_id=row["workflow_id"],
        claim_id=claim.get("id", ""),
        original_claim=claim,
        matched_skill_ids=row["matched_skill_ids"] or [],
        attempts=[_attempt(a) for a in sorted(row["attempts"], key=lambda a: a["created_at"])],
        saved=row["status"] == "done",
    )


async def create_tutor_session(learner_name: str, workflow_id: str, claim: dict, matched_skill_ids: list[str]) -> TutorSession:
    row = (
        await _t("sessions")
        .insert(
            {
                "kind": "training",
                "user_id": await _user_id(learner_name, "trainee"),
                "workflow_id": workflow_id,
                "claim": claim,
                "matched_skill_ids": matched_skill_ids,
            }
        )
        .execute()
    ).data[0]
    return _tutor({**row, "users": {"name": learner_name}, "attempts": []})


async def get_tutor_session(session_id: str) -> TutorSession | None:
    sid = _id(session_id)
    if not sid:
        return None
    rows = (await _t("sessions").select(_TUTOR_COLS).eq("id", sid).eq("kind", "training").execute()).data
    return _tutor(rows[0]) if rows else None


async def record_prediction(ts: TutorSession, skill_id: str, prediction: str, correct: bool, feedback: str) -> None:
    await _t("attempts").insert(
        {
            "session_id": ts.id,
            "skill_id": skill_id,
            "trainee_id": await _user_id(ts.learner_name, "trainee"),
            "prediction": prediction,
            # A wrong prediction gets the expert's answer explained, so it counts as a hint, not a mistake.
            "outcome": "correct" if correct else "hinted",
            "tutor_message": feedback,
        }
    ).execute()
    await _t("interventions").insert(
        {"session_id": ts.id, "skill_id": skill_id, "kind": "praise" if correct else "explain", "message": feedback}
    ).execute()


async def record_save_check(ts: TutorSession, blocked: dict[str, str]) -> None:
    """One attempt per matched skill. `blocked` maps the skill ids whose guardrail failed to the message shown."""
    if ts.matched_skill_ids:
        trainee_id = await _user_id(ts.learner_name, "trainee")
        await _t("attempts").insert(
            [
                {
                    "session_id": ts.id,
                    "skill_id": sid,
                    "trainee_id": trainee_id,
                    "outcome": "caught" if sid in blocked else "correct",
                    "blocked_save": sid in blocked,
                    "tutor_message": blocked.get(sid),
                }
                for sid in ts.matched_skill_ids
            ]
        ).execute()
    if blocked:
        await _t("interventions").insert(
            [{"session_id": ts.id, "skill_id": sid, "kind": "block", "message": msg} for sid, msg in blocked.items()]
        ).execute()
    saved = {"status": "done", "ended_at": _now()} if not blocked else {"status": "live"}
    await _t("sessions").update(saved).eq("id", ts.id).execute()


# --- Practice claims (seed data, not in the database) -----------------------
def load_claims() -> list[dict]:
    return json.loads((SEED_DIR / "claims.json").read_text(encoding="utf-8"))


def get_claim(claim_id: str) -> dict | None:
    return next((c for c in load_claims() if c["id"] == claim_id), None)
