"""Workflows: what experts teach and trainees practice. Each one defines what its records look like."""

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import store
from ..extract import NoFields, extract_record
from ..models import PracticeCase, RecordField, Workflow, WorkMap

router = APIRouter(prefix="/api", tags=["workflows"])

_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


async def _get(workflow_id: str) -> Workflow:
    wf = await store.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "workflow not found")
    return wf


@router.get("/workflows")
async def list_workflows() -> list[Workflow]:
    return await store.list_workflows()


@router.get("/workflows/published")
async def list_published(learner: str | None = None) -> list[Workflow]:
    """What trainees can practice: workflows with approved skills, plus the learner's progress on each."""
    return await store.list_published_workflows(learner)


class CreateBody(BaseModel):
    name: str
    app: str = ""
    description: str = ""


@router.post("/workflows")
async def create(body: CreateBody) -> Workflow:
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    return await store.create_workflow(body.name.strip(), body.app.strip(), body.description.strip())


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: str) -> Workflow:
    return await _get(workflow_id)


class UpdateBody(BaseModel):
    name: str | None = None
    app: str | None = None
    description: str | None = None
    fields: list[RecordField] | None = None


@router.patch("/workflows/{workflow_id}")
async def update(workflow_id: str, body: UpdateBody) -> Workflow:
    """Lets the expert rename the workflow or correct its record fields."""
    wf = await _get(workflow_id)
    if body.name is not None and not body.name.strip():
        raise HTTPException(400, "name is required")
    if body.fields is not None:
        names = [f.name for f in body.fields]
        bad = [n for n in names if not _FIELD_NAME.match(n)]
        if bad:
            raise HTTPException(400, f"field names must be snake_case: {', '.join(bad)}")
        if len(set(names)) != len(names):
            raise HTTPException(400, "field names must be unique")
    return await store.update_workflow(wf.id, name=body.name, app=body.app, description=body.description, fields=body.fields)


@router.get("/workflows/{workflow_id}/workmaps")
async def workmaps(workflow_id: str) -> list[WorkMap]:
    """Every recorded expert session in the workflow with its skills, newest first, for review."""
    wf = await _get(workflow_id)
    return await store.list_workmaps(wf.id)


# --- Practice cases ---------------------------------------------------------
@router.get("/workflows/{workflow_id}/cases")
async def list_cases(workflow_id: str) -> list[PracticeCase]:
    wf = await _get(workflow_id)
    return await store.list_cases(wf.id)


class CaseBody(BaseModel):
    label: str
    # Either the record itself, or a snapshot of the page showing it (read with the workflow's fields).
    data: dict | None = None
    page: str | None = None


@router.post("/workflows/{workflow_id}/cases")
async def create_case(workflow_id: str, body: CaseBody) -> PracticeCase:
    """Saves a record as it arrives, before anyone works it, for trainees to practice on."""
    wf = await _get(workflow_id)
    if not body.label.strip():
        raise HTTPException(400, "label is required")
    if body.data is not None:
        data = body.data
    elif body.page:
        try:
            data = await extract_record(wf, body.page)
        except NoFields as e:
            raise HTTPException(400, str(e))
    else:
        raise HTTPException(400, "send data or page")
    return await store.create_case(wf.id, body.label.strip(), data)


@router.delete("/cases/{case_id}")
async def delete_case(case_id: str) -> dict:
    if not await store.delete_case(case_id):
        raise HTTPException(404, "practice case not found")
    return {"ok": True}
