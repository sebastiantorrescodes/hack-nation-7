from fastapi import APIRouter, HTTPException

from .. import store
from ..models import CLAIM_FIELDS, WorkMap

router = APIRouter(prefix="/api", tags=["workmaps"])


@router.get("/workmaps")
def list_workmaps() -> list[WorkMap]:
    return store.list_all("workmaps", WorkMap)


@router.get("/workmaps/{workmap_id}")
def get_workmap(workmap_id: str) -> WorkMap:
    wm = store.load("workmaps", workmap_id, WorkMap)
    if not wm:
        raise HTTPException(404, "work map not found")
    return wm


@router.put("/workmaps/{workmap_id}")
def update_workmap(workmap_id: str, wm: WorkMap) -> WorkMap:
    """Lets the expert correct a skill record after review."""
    if wm.id != workmap_id:
        raise HTTPException(400, "id mismatch")
    store.save("workmaps", wm)
    return wm


@router.get("/claim-fields")
def claim_fields() -> dict[str, str]:
    return CLAIM_FIELDS
