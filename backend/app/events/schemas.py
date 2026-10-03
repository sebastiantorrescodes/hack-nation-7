# This file defines the common event format used by browser and vision inputs.

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..vision.schemas import BoundingBox


EventType = Literal["click", "field_change", "navigation", "submit_attempt", "screen_observation"]


class ObservedEvent(BaseModel):
    """Retain observed facts, their source, and optional capture metadata."""

    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1)
    source: Literal["browser", "vision"]
    source_event_id: str | None = None
    timestamp_ms: int | None = Field(default=None, ge=0)
    event_type: EventType
    target: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    page: str = ""
    description: str = ""
    x_norm: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    y_norm: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    bounding_box: BoundingBox | None = None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_event(self) -> "ObservedEvent":
        """Reject partial locations and incomplete field-change records."""
        if (self.x_norm is None) != (self.y_norm is None):
            raise ValueError("Provide both normalized coordinates or neither")
        if self.event_type == "field_change":
            if not self.target or not self.target.strip() or self.new_value is None:
                raise ValueError("A field change requires a target and a known new value")
        return self
