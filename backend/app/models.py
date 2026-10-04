"""Domain models: workflows and their record fields, capture sessions, the Work Map (skill records),
practice cases and tutor sessions. Nothing here is specific to one industry: a workflow says what its
records look like, and skills are conditions over those fields."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FieldType = Literal["string", "number", "boolean", "list"]


class RecordField(BaseModel):
    """One field of the records a workflow works on, e.g. amount (number) on an expense report."""

    name: str  # snake_case key, used in conditions
    type: FieldType
    description: str = ""


Op = Literal["eq", "neq", "in", "not_in", "contains", "not_contains", "gt", "lt", "is_true", "is_false", "empty", "not_empty"]
OPS: list[str] = list(Op.__args__)


class Condition(BaseModel):
    field: str
    op: Op
    values: list[str] = Field(default_factory=list)


ActionKind = Literal["set_value", "add_value", "remove_value", "hold", "escalate", "request_info", "submit", "other"]
ACTION_KINDS: list[str] = list(ActionKind.__args__)


class Action(BaseModel):
    kind: ActionKind
    detail: str


class Guardrail(BaseModel):
    description: str
    # Conditions that must all hold on the record at save time whenever the trigger matched.
    must: list[Condition]


class EvidenceRef(BaseModel):
    t: float  # seconds since session start
    quote: str
    segment_id: int | None = None
    event_id: int | None = None


class Skill(BaseModel):
    id: str
    title: str
    trigger: list[Condition]  # all must match
    action: Action
    expert_explanation: str  # the expert's own words, lightly cleaned
    guardrail: Guardrail
    evidence: list[EvidenceRef] = Field(default_factory=list)
    # Only approved skills are published to trainees.
    status: Literal["draft", "approved", "rejected"] = "draft"
    expert_name: str = ""
    version: int = 1
    supersedes: str | None = None


class WorkMap(BaseModel):
    """The skills captured in one expert session."""

    id: str
    session_id: str
    workflow_id: str = ""
    expert_name: str
    summary: str
    recorded_at: float = 0
    skills: list[Skill]
    revision: int = 0
    teach_back_confirmed: bool = False


class Workflow(BaseModel):
    id: str
    name: str
    app: str
    description: str = ""
    fields: list[RecordField] = Field(default_factory=list)
    approved_skills: int = 0
    draft_skills: int = 0
    sessions: int = 0  # capture sessions with a Work Map
    mastered_skills: int | None = None  # set when listed for a trainee


class PracticeCase(BaseModel):
    """A record as it arrives, before anyone has worked it."""

    id: str
    workflow_id: str
    label: str
    data: dict


# --- Capture ----------------------------------------------------------------
class TranscriptTurn(BaseModel):
    client_id: str | None = None
    segment_id: int | None = None
    question_id: str | None = None
    answer_id: str | None = None
    t: float
    role: Literal["expert", "agent"]
    text: str


class ScreenEvent(BaseModel):
    event_id: int | None = None
    client_event_id: str | None = None
    t: float
    kind: str
    description: str
    record_fields: dict = Field(default_factory=dict)
    # Canonical browser facts remain distinct from model-extracted record fields.
    observed_action: dict | None = None
    is_decision_point: bool = False
    ask_why: str | None = None
    question_id: str | None = None


class CaptureSession(BaseModel):
    id: str
    workflow_id: str = ""
    expert_name: str
    started_at: float
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    events: list[ScreenEvent] = Field(default_factory=list)
    last_screen_summary: str = ""
    workmap_id: str | None = None
    phase: Literal["capture", "debrief", "finished"] = "capture"
    workmap_revision: int = 0


# --- Tutor ------------------------------------------------------------------
class Attempt(BaseModel):
    skill_id: str
    kind: Literal["prediction", "save_check"]
    correct: bool
    detail: str


class TutorSession(BaseModel):
    id: str
    learner_name: str
    workflow_id: str  # trainees practice a workflow's approved skills
    case_id: str  # practice case id, or "live" when read from the open app
    original_record: dict  # the record as it arrived; triggers are evaluated against this
    matched_skill_ids: list[str]
    attempts: list[Attempt] = Field(default_factory=list)
    saved: bool = False
    skill_snapshot: list[Skill] = Field(default_factory=list)
    workflow_snapshot: Workflow | None = None
