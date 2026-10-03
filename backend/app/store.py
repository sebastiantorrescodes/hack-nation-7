"""Tiny JSON-file store. Good enough for a hackathon; swap for a DB later."""

import json
from typing import TypeVar

from pydantic import BaseModel

from .config import DATA_DIR, SEED_DIR

T = TypeVar("T", bound=BaseModel)


def _dir(kind: str):
    d = DATA_DIR / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def save(kind: str, obj: BaseModel) -> None:
    (_dir(kind) / f"{obj.id}.json").write_text(obj.model_dump_json(indent=2), encoding="utf-8")


def load(kind: str, id: str, cls: type[T]) -> T | None:
    path = _dir(kind) / f"{id}.json"
    if not path.exists():
        return None
    return cls.model_validate_json(path.read_text(encoding="utf-8"))


def list_all(kind: str, cls: type[T]) -> list[T]:
    return [cls.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(_dir(kind).glob("*.json"))]


def load_claims() -> list[dict]:
    return json.loads((SEED_DIR / "claims.json").read_text(encoding="utf-8"))


def get_claim(claim_id: str) -> dict | None:
    return next((c for c in load_claims() if c["id"] == claim_id), None)
