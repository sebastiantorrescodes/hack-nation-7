"""Tutor: guide a new hire through an unseen claim using the Work Map."""

import json
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import rules, store
from ..extract import extract_claim
from ..llm import GRADE_SCHEMA, REPORT_SCHEMA, structured
from ..models import Attempt, Condition, Skill, TutorSession, WorkMap

router = APIRouter(prefix="/api/tutor", tags=["tutor"])


def _session(session_id: str) -> tuple[TutorSession, WorkMap, dict]:
    ts = store.load("tutor", session_id, TutorSession)
    if not ts:
        raise HTTPException(404, "tutor session not found")
    wm = store.load("workmaps", ts.workmap_id, WorkMap)
    if not wm:
        raise HTTPException(404, "work map missing")
    return ts, wm, ts.original_claim


def _skill(wm: WorkMap, skill_id: str) -> Skill:
    sk = next((s for s in wm.skills if s.id == skill_id), None)
    if not sk:
        raise HTTPException(404, "skill not found")
    return sk


def _fmt(c: Condition) -> str:
    return f"{c.field} {c.op} {', '.join(c.values)}".strip()


@router.get("/claims")
def claims() -> list[dict]:
    return store.load_claims()


class StartBody(BaseModel):
    learner_name: str
    workmap_id: str
    # Either a seed practice claim, or a snapshot of the live OpenEMR page.
    claim_id: str | None = None
    page: str | None = None


@router.post("/sessions")
async def start(body: StartBody) -> dict:
    wm = store.load("workmaps", body.workmap_id, WorkMap)
    if not wm:
        raise HTTPException(404, "work map not found")
    if body.claim_id:
        claim = store.get_claim(body.claim_id)
        if not claim:
            raise HTTPException(404, "claim not found")
    elif body.page:
        claim = {"id": "live", "label": "Claim open in OpenEMR", **await extract_claim(body.page)}
    else:
        raise HTTPException(400, "send claim_id or page")
    matched = rules.triggered_skills(wm.skills, claim)
    ts = TutorSession(
        id=uuid.uuid4().hex[:12],
        learner_name=body.learner_name,
        workmap_id=wm.id,
        claim_id=claim["id"],
        original_claim=claim,
        matched_skill_ids=[s.id for s in matched],
    )
    store.save("tutor", ts)
    # Only reveal *that* a decision point exists, not the answer: the learner predicts first.
    return {
        "session": ts,
        "claim": claim,
        "decision_points": [{"skill_id": s.id, "title": s.title, "trigger": [_fmt(c) for c in s.trigger]} for s in matched],
    }


class PredictBody(BaseModel):
    skill_id: str
    prediction: str


GRADE_SYSTEM = """You are a patient medical-billing tutor. A new biller was shown a claim and asked what they
would do at a decision point, before acting. Compare their prediction with what the senior expert does.
Mark correct if they would take the same action for substantially the same reason (wording can differ;
partial credit counts as correct only if the core action is right). Feedback: 2-4 sentences, warm and direct.
If wrong, explain the right move using the expert's own words (quote them). If right, reinforce why, again
in the expert's words, and add one nuance they might have missed."""


@router.post("/sessions/{session_id}/predict")
async def predict(session_id: str, body: PredictBody) -> dict:
    ts, wm, claim = _session(session_id)
    sk = _skill(wm, body.skill_id)
    result = await structured(
        effort="low",
        max_tokens=4000,
        system=GRADE_SYSTEM,
        content=(
            f"Claim:\n{json.dumps(claim, indent=1)}\n\n"
            f"Decision point: {sk.title}\n"
            f"Expert action: {sk.action.kind} - {sk.action.detail}\n"
            f"Expert explanation (their words): \"{sk.expert_explanation}\"\n\n"
            f"Learner's prediction: {body.prediction}"
        ),
        schema=GRADE_SCHEMA,
    )
    ts.attempts.append(Attempt(skill_id=sk.id, kind="prediction", correct=result["correct"], detail=body.prediction))
    store.save("tutor", ts)
    return {**result, "expert_action": sk.action, "expert_explanation": sk.expert_explanation, "expert_name": wm.expert_name}


class CheckBody(BaseModel):
    # Either the edited claim (practice form) or a snapshot of the live OpenEMR page at Save time.
    claim: dict | None = None
    page: str | None = None


@router.post("/sessions/{session_id}/check")
async def check_before_save(session_id: str, body: CheckBody) -> dict:
    """Runs every triggered guardrail against the claim about to be saved. Blocks the save on any violation."""
    ts, wm, original = _session(session_id)
    if body.claim is not None:
        edited = body.claim
    elif body.page:
        edited = await extract_claim(body.page)
    else:
        raise HTTPException(400, "send claim or page")
    violations = rules.guardrail_violations(wm.skills, original, edited)
    failed_ids = {sk.id for sk, _ in violations}
    for sid in ts.matched_skill_ids:
        ts.attempts.append(Attempt(skill_id=sid, kind="save_check", correct=sid not in failed_ids, detail=""))
    ts.saved = not violations
    store.save("tutor", ts)
    return {
        "ok": not violations,
        "claim_seen": edited,
        "violations": [
            {
                "skill_id": sk.id,
                "title": sk.title,
                "guardrail": sk.guardrail.description,
                "failed": [_fmt(c) for c in failed],
                "expert_explanation": sk.expert_explanation,
                "expert_name": wm.expert_name,
            }
            for sk, failed in violations
        ],
    }


REPORT_SYSTEM = """You write a short mastery report for a new medical biller after a practice claim.
For each skill: mastered (predicted right AND passed the save check first try), practicing (got there with
help), or not_yet (wrong prediction and failed the save check, or never attempted). Headline: one encouraging,
honest sentence. practice_next: 1-3 concrete things to practice, phrased for the learner."""


@router.get("/sessions/{session_id}/report")
async def report(session_id: str) -> dict:
    ts, wm, _ = _session(session_id)
    skills = [s for s in wm.skills if s.id in ts.matched_skill_ids]
    if not skills:
        return {"headline": "This claim had no decision points from the Work Map.", "skills": [], "practice_next": []}
    log = [a.model_dump() for a in ts.attempts]
    result = await structured(
        effort="low",
        max_tokens=4000,
        system=REPORT_SYSTEM,
        content=(
            f"Learner: {ts.learner_name}\n\nSkills on this claim:\n"
            + "\n".join(f"- {s.id}: {s.title}" for s in skills)
            + f"\n\nAttempt log (in order):\n{json.dumps(log, indent=1)}"
        ),
        schema=REPORT_SCHEMA,
    )
    titles = {s.id: s.title for s in skills}
    for row in result["skills"]:
        row["title"] = titles.get(row["skill_id"], row["skill_id"])
    return result
