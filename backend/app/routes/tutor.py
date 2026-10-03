"""Tutor: guide a newcomer through an unseen record using a workflow's published Work Map (its approved skills)."""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import rules, store
from ..extract import NoFields, extract_record
from ..llm import GRADE_SCHEMA, REPORT_SCHEMA, structured, workflow_doc
from ..models import Condition, Skill, TutorSession, Workflow

router = APIRouter(prefix="/api/tutor", tags=["tutor"])


async def _workflow(workflow_id: str) -> Workflow:
    wf = await store.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "workflow not found")
    return wf


async def _session(session_id: str) -> tuple[TutorSession, Workflow, list[Skill]]:
    ts = await store.get_tutor_session(session_id)
    if not ts:
        raise HTTPException(404, "tutor session not found")
    return ts, await _workflow(ts.workflow_id), await store.published_skills(ts.workflow_id)


async def _extract(wf: Workflow, page: str) -> dict:
    try:
        return await extract_record(wf, page)
    except NoFields as e:
        raise HTTPException(400, str(e))


def _skill(skills: list[Skill], skill_id: str) -> Skill:
    sk = next((s for s in skills if s.id == skill_id), None)
    if not sk:
        raise HTTPException(404, "skill not found")
    return sk


def _fmt(c: Condition) -> str:
    return f"{c.field} {c.op} {', '.join(c.values)}".strip()


class StartBody(BaseModel):
    learner_name: str
    workflow_id: str
    # Either one of the workflow's practice cases, or a snapshot of the record open in the app.
    case_id: str | None = None
    page: str | None = None


@router.post("/sessions")
async def start(body: StartBody) -> dict:
    wf = await _workflow(body.workflow_id)
    skills = await store.published_skills(wf.id)
    if not skills:
        raise HTTPException(400, "this workflow has no approved skills yet")
    if body.case_id:
        case = await store.get_case(body.case_id)
        if not case or case.workflow_id != wf.id:
            raise HTTPException(404, "practice case not found")
        record = {"id": case.id, "label": case.label, **case.data}
    elif body.page:
        record = {"id": "live", "label": f"Open in {wf.app or 'the app'}", **await _extract(wf, body.page)}
    else:
        raise HTTPException(400, "send case_id or page")
    matched = rules.triggered_skills(skills, record)
    ts = await store.create_tutor_session(body.learner_name, wf.id, record, [s.id for s in matched])
    # Only reveal *that* a decision point exists, not the answer: the learner predicts first.
    return {
        "session": ts,
        "record": record,
        "decision_points": [{"skill_id": s.id, "title": s.title, "trigger": [_fmt(c) for c in s.trigger]} for s in matched],
    }


class PredictBody(BaseModel):
    skill_id: str
    prediction: str


GRADE_SYSTEM = """You are a patient tutor helping a newcomer learn a job from an experienced colleague. The newcomer
was shown a record and asked what they would do at a decision point, before acting. Compare their prediction
with what the expert does. Mark correct if they would take the same action for substantially the same reason
(wording can differ; partial credit counts as correct only if the core action is right). Feedback: 2-4
sentences, warm and direct. If wrong, explain the right move using the expert's own words (quote them). If
right, reinforce why, again in the expert's words, and add one nuance they might have missed."""


@router.post("/sessions/{session_id}/predict")
async def predict(session_id: str, body: PredictBody) -> dict:
    ts, wf, skills = await _session(session_id)
    sk = _skill(skills, body.skill_id)
    result = await structured(
        effort="low",
        max_tokens=4000,
        system=GRADE_SYSTEM,
        content=(
            f"{workflow_doc(wf)}\n\n"
            f"Record:\n{json.dumps(ts.original_record, indent=1)}\n\n"
            f"Decision point: {sk.title}\n"
            f"Expert action: {sk.action.kind} - {sk.action.detail}\n"
            f"Expert explanation (their words): \"{sk.expert_explanation}\"\n\n"
            f"Learner's prediction: {body.prediction}"
        ),
        schema=GRADE_SCHEMA,
    )
    await store.record_prediction(ts, sk.id, body.prediction, result["correct"], result["feedback"])
    return {**result, "expert_action": sk.action, "expert_explanation": sk.expert_explanation, "expert_name": sk.expert_name}


class CheckBody(BaseModel):
    # Either the edited record (practice form) or a snapshot of the app's page at Save time.
    record: dict | None = None
    page: str | None = None


@router.post("/sessions/{session_id}/check")
async def check_before_save(session_id: str, body: CheckBody) -> dict:
    """Runs every triggered guardrail against the record about to be saved. Blocks the save on any violation."""
    ts, wf, skills = await _session(session_id)
    if body.record is not None:
        edited = body.record
    elif body.page:
        edited = await _extract(wf, body.page)
    else:
        raise HTTPException(400, "send record or page")
    violations = rules.guardrail_violations(skills, ts.original_record, edited)
    await store.record_save_check(ts, {sk.id: sk.guardrail.description for sk, _ in violations})
    return {
        "ok": not violations,
        "record_seen": edited,
        "violations": [
            {
                "skill_id": sk.id,
                "title": sk.title,
                "guardrail": sk.guardrail.description,
                "failed": [_fmt(c) for c in failed],
                "expert_explanation": sk.expert_explanation,
                "expert_name": sk.expert_name,
            }
            for sk, failed in violations
        ],
    }


REPORT_SYSTEM = """You write a short mastery report for a newcomer after they worked a practice record.
For each skill: mastered (predicted right AND passed the save check first try), practicing (got there with
help), or not_yet (wrong prediction and failed the save check, or never attempted). Headline: one encouraging,
honest sentence. practice_next: 1-3 concrete things to practice, phrased for the learner."""


@router.get("/sessions/{session_id}/report")
async def report(session_id: str) -> dict:
    ts, wf, published = await _session(session_id)
    skills = [s for s in published if s.id in ts.matched_skill_ids]
    if not skills:
        return {"headline": "This record had no decision points from the Work Map.", "skills": [], "practice_next": []}
    log = [a.model_dump() for a in ts.attempts]
    result = await structured(
        effort="low",
        max_tokens=4000,
        system=REPORT_SYSTEM,
        content=(
            f"Workflow: {wf.name}\nLearner: {ts.learner_name}\n\nSkills on this record:\n"
            + "\n".join(f"- {s.id}: {s.title}" for s in skills)
            + f"\n\nAttempt log (in order):\n{json.dumps(log, indent=1)}"
        ),
        schema=REPORT_SCHEMA,
    )
    titles = {s.id: s.title for s in skills}
    for row in result["skills"]:
        row["title"] = titles.get(row["skill_id"], row["skill_id"])
    return result
