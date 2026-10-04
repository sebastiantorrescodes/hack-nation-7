from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import store
from ..models import Skill, WorkMap

router = APIRouter(prefix="/api", tags=["workmaps"])


@router.get("/workmaps")
async def list_workmaps() -> list[WorkMap]:
    return await store.list_workmaps()


@router.get("/workmaps/{workmap_id}")
async def get_workmap(workmap_id: str) -> WorkMap:
    wm = await store.get_workmap(workmap_id)
    if not wm:
        raise HTTPException(404, "work map not found")
    return wm


@router.put("/workmaps/{workmap_id}")
async def update_workmap(workmap_id: str, wm: WorkMap) -> WorkMap:
    """Lets the expert correct a skill record after review."""
    if wm.id != workmap_id:
        raise HTTPException(400, "id mismatch")
    updated = await store.update_workmap(wm)
    if not updated:
        raise HTTPException(404, "work map not found")
    return updated


class StatusBody(BaseModel):
    status: Literal["draft", "approved", "rejected"]
    expected_version: int


@router.patch("/skills/{skill_id}")
async def review_skill(skill_id: str, body: StatusBody) -> Skill:
    """Expert review: approving publishes the skill to trainees, rejecting removes it from the Work Map."""
    sk = await store.set_skill_status(skill_id, body.status, body.expected_version)
    if not sk:
        raise HTTPException(404, "skill not found")
    return sk
