"""Domain models: capture sessions, the Work Map (skill records), claims and tutor sessions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- Claim vocabulary -------------------------------------------------------
# Skill triggers and guardrails are written as conditions over these fields, so
# the tutor can evaluate them deterministically. Keep this in sync with
# seed/claims.json and the claim form in the frontend.
CLAIM_FIELDS: dict[str, str] = {
    "payer": "string - payer name, e.g. 'Medicare', 'Aetna', 'BCBS'",
    "patient_age": "number - patient age in years",
    "place_of_service": "string - POS code, e.g. '11' office, '22' outpatient hospital",
    "visit_type": "string - 'new' or 'established'",
    "cpt_codes": "list of strings - CPT/HCPCS codes on the claim",
    "icd10_codes": "list of strings - ICD-10 diagnosis codes",
    "modifiers": "list of strings - modifiers applied, e.g. '25', '59', 'GT'",
    "same_day_procedure": "boolean - a procedure was performed on the same day as the E/M visit",
    "prior_auth_on_file": "boolean - prior authorization is on file",
    "documentation_complete": "boolean - provider note is signed and supports the codes",
    "units": "number - total units billed",
    "charge_amount": "number - total charge in USD",
    "status": "string - 'draft', 'ready', 'held', 'submitted'",
    "physician_query": "string - open query to the physician, empty if none",
}

Op = Literal["eq", "neq", "in", "not_in", "contains", "not_contains", "gt", "lt", "is_true", "is_false", "empty", "not_empty"]


class Condition(BaseModel):
    field: str
    op: Op
    values: list[str] = Field(default_factory=list)


class Action(BaseModel):
    kind: Literal["add_modifier", "remove_modifier", "change_code", "hold_claim", "query_physician", "submit", "other"]
    detail: str


class Guardrail(BaseModel):
    description: str
    # Conditions that must all hold on the claim at save time whenever the trigger matched.
    must: list[Condition]


class EvidenceRef(BaseModel):
    t: float  # seconds since session start
    quote: str


class Skill(BaseModel):
    id: str
    title: str
    trigger: list[Condition]  # all must match
    action: Action
    expert_explanation: str  # the expert's own words, lightly cleaned
    guardrail: Guardrail
    evidence: list[EvidenceRef] = Field(default_factory=list)


class WorkMap(BaseModel):
    id: str
    session_id: str
    expert_name: str
    summary: str
    skills: list[Skill]


# --- Capture ----------------------------------------------------------------
class TranscriptTurn(BaseModel):
    t: float
    role: Literal["expert", "agent"]
    text: str


class ScreenEvent(BaseModel):
    t: float
    kind: str
    description: str
    claim_fields: dict = Field(default_factory=dict)
    is_decision_point: bool = False
    ask_why: str | None = None


class CaptureSession(BaseModel):
    id: str
    expert_name: str
    started_at: float
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    events: list[ScreenEvent] = Field(default_factory=list)
    last_screen_summary: str = ""
    workmap_id: str | None = None


# --- Tutor ------------------------------------------------------------------
class Attempt(BaseModel):
    skill_id: str
    kind: Literal["prediction", "save_check"]
    correct: bool
    detail: str


class TutorSession(BaseModel):
    id: str
    learner_name: str
    workmap_id: str
    claim_id: str  # seed claim id, or "live" when read from the OpenEMR page
    original_claim: dict  # the claim as it arrived; triggers are evaluated against this
    matched_skill_ids: list[str]
    attempts: list[Attempt] = Field(default_factory=list)
    saved: bool = False
