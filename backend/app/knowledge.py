"""The shared evidence and rule contract for every route into draft skills."""

import math
import re

from .models import EvidenceRef, RecordField, Skill


class KnowledgeError(ValueError):
    pass


def resolve_evidence(e: EvidenceRef, segments: list[dict], events: list[dict]) -> EvidenceRef:
    candidates = [s for s in segments if s.get("speaker") == "expert" and not s.get("off_record")
                  and e.quote.strip() and e.quote.casefold() in s["text"].casefold()]
    if e.segment_id is not None:
        candidates = [s for s in candidates if s["id"] == e.segment_id]
    else:
        # Legacy timestamps remain accepted only when the exact quote and time identify one segment.
        candidates = [s for s in candidates if abs((s.get("t_start_ms") or 0) / 1000 - e.t) <= .001]
    if len(candidates) != 1:
        raise KnowledgeError("Evidence must identify one recorded, on-record expert quote.")
    if e.event_id is None or not any(x["id"] == e.event_id for x in events):
        raise KnowledgeError("Each expert quote must reference an observed event in this session.")
    return e.model_copy(update={"segment_id": candidates[0]["id"]})


def validate_skill(skill: Skill, fields: list[RecordField]) -> None:
    if not all(x.strip() for x in (skill.title, skill.action.detail, skill.expert_explanation, skill.guardrail.description)):
        raise KnowledgeError("A skill needs its action, expert explanation and guardrail description.")
    if not skill.trigger or not skill.guardrail.must or not skill.evidence:
        raise KnowledgeError("A teachable skill needs nonempty triggers, save conditions and expert evidence.")
    definitions = {f.name: f.type for f in fields}
    if len(definitions) != len(fields) or any(not re.fullmatch(r"[a-z][a-z0-9_]*", name) for name in definitions):
        raise KnowledgeError("Record fields must have unique snake_case names.")
    for c in skill.trigger + skill.guardrail.must:
        if c.field not in definitions:
            raise KnowledgeError("A condition references an undefined record field.")
        unary = c.op in {"is_true", "is_false", "empty", "not_empty"}
        if (unary and c.values) or (not unary and not c.values):
            raise KnowledgeError("A condition has missing or unexpected comparison values.")
        if c.op in {"eq", "neq", "gt", "lt"} and len(c.values) != 1:
            raise KnowledgeError("This comparison requires exactly one value.")
        if c.op in {"is_true", "is_false"} and definitions[c.field] != "boolean":
            raise KnowledgeError("Boolean conditions require boolean fields.")
        if c.op in {"gt", "lt"}:
            try:
                valid = definitions[c.field] == "number" and math.isfinite(float(c.values[0]))
            except ValueError:
                valid = False
            if not valid:
                raise KnowledgeError("Numeric conditions require finite numeric values and numeric fields.")


def draft_payload(skills: list[Skill], fields: list[RecordField], segments: list[dict], events: list[dict]) -> list[dict]:
    """Validate the whole build before any writes; publication remains an explicit operation."""
    result = []
    for skill in skills:
        validate_skill(skill, fields)
        evidence = [resolve_evidence(e, segments, events) for e in skill.evidence]
        result.append({**skill.model_dump(mode="json"), "evidence": [e.model_dump() for e in evidence]})
    if not result:
        raise KnowledgeError("No evidence-backed skills yet. Capture an expert explanation before building.")
    return result


def teach_back(workmap) -> str:
    if any(s.status == "draft" for s in workmap.skills):
        raise KnowledgeError("Review every draft before confirming the teach-back.")
    approved = [s for s in workmap.skills if s.status == "approved"]
    if not approved:
        raise KnowledgeError("Approve at least one evidence-backed skill first.")
    return "Here is what I learned from this session. " + " ".join(
        f"{s.title}. The action is: {s.action.detail}. Your reason was: {s.expert_explanation}. "
        f"Before saving, verify: {s.guardrail.description}."
        for s in approved)
