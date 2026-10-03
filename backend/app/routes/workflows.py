"""Workflows: what experts teach and trainees practice."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import store
from ..models import Workflow, WorkMap

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


async def _get(workflow_id: str) -> Workflow:
    wf = await store.get_workflow(workflow_id)
    if not wf:
        raise HTTPException(404, "workflow not found")
    return wf


@router.get("")
async def list_workflows() -> list[Workflow]:
    return await store.list_workflows()


@router.get("/published")
async def list_published(learner: str | None = None) -> list[Workflow]:
    """What trainees can practice: workflows with approved skills, plus the learner's progress on each."""
    return await store.list_published_workflows(learner)


class CreateBody(BaseModel):
    name: str
    app: str = "OpenEMR"


@router.post("")
async def create(body: CreateBody) -> Workflow:
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    return await store.create_workflow(body.name.strip(), body.app.strip() or "OpenEMR")


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str) -> Workflow:
    return await _get(workflow_id)


@router.get("/{workflow_id}/workmaps")
async def workmaps(workflow_id: str) -> list[WorkMap]:
    """Every recorded expert session in the workflow with its skills, newest first, for review."""
    wf = await _get(workflow_id)
    return await store.list_workmaps(wf.id)
