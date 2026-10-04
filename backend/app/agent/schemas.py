# This file defines agent decisions, expert evidence, and the in-memory Work Map.

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..events.schemas import ObservedEvent


QuestionType = Literal["reason", "guardrail", "exception", "clarification"]


class QuestionRequest(BaseModel):
    """Ask one short question tied to a recorded screen event."""

    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str = Field(min_length=1)
    text: str = Field(min_length=1, max_length=300)
    question_type: QuestionType


class ExpertQuestion(QuestionRequest):
    """Retain the question identifier and when it was queued."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    phase: Literal["capture", "debrief"]


class ExpertAnswer(BaseModel):
    """Retain the expert's exact answer and its question and screen references."""

    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(default_factory=lambda: uuid4().hex)
    question_id: str
    event_id: str
    text: str = Field(min_length=1)
    segment_id: int | None = None


class StepProposal(BaseModel):
    """Describe proposed knowledge with references to recorded expert evidence."""

    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1)
    action: str | None
    reason: str | None
    # None means unknown; [] means the expert reported no guardrail.
    guardrails: list[str] | None
    event_ids: list[str] = Field(min_length=1)
    answer_ids: list[str] = Field(min_length=1)


class WorkflowStep(StepProposal):
    """Keep proposed knowledge separate from explicitly confirmed knowledge."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    status: Literal["proposed", "confirmed"] = "proposed"
    confirmed_by: str | None = None


class WorkMapState(BaseModel):
    """Hold one session's evidence and Work Map without persistent storage."""

    model_config = ConfigDict(extra="forbid", strict=True)
    expert_name: str = Field(min_length=1)
    phase: Literal["capture", "debrief", "finished"] = "capture"
    events: dict[str, ObservedEvent] = Field(default_factory=dict)
    questions: list[ExpertQuestion] = Field(default_factory=list)
    delivered_question_ids: list[str] = Field(default_factory=list)
    delivery_segments: dict[str, int] = Field(default_factory=dict)
    answers: list[ExpertAnswer] = Field(default_factory=list)
    steps: list[WorkflowStep] = Field(default_factory=list)
    teach_back_confirmed_by: str | None = None


class CaptureContext(BaseModel):
    """Require the capture caller to report activity and a genuine pause."""

    model_config = ConfigDict(extra="forbid", strict=True)
    expert_paused: bool = False
    expert_busy: bool = False
    expert_speaking: bool = False
    task_finished: bool = False


class AgentDecision(BaseModel):
    """Allow exactly one bounded action and its matching payload."""

    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["ask_question", "save_step", "update_step", "wait", "start_debrief", "finish_session"]
    reason: str
    question: QuestionRequest | None
    step: StepProposal | None
    step_id: str | None

    @model_validator(mode="after")
    def check_payload(self) -> "AgentDecision":
        """Reject missing arguments and arguments for an unrelated tool."""
        if (self.question is not None) != (self.action == "ask_question"):
            raise ValueError("Only ask_question requires a question")
        if (self.step is not None) != (self.action in {"save_step", "update_step"}):
            raise ValueError("Only save_step and update_step require a step")
        if (self.step_id is not None) != (self.action == "update_step"):
            raise ValueError("Only update_step requires a step_id")
        return self
