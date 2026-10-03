# This file defines visible screen facts and validates their coordinates.

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BoundingBox(BaseModel):
    """Locate an element with coordinates normalized to the original image."""

    model_config = ConfigDict(extra="forbid", strict=True)

    x_min: float = Field(ge=0, le=1)
    y_min: float = Field(ge=0, le=1)
    x_max: float = Field(ge=0, le=1)
    y_max: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def check_order(self) -> "BoundingBox":
        """Reject inverted or zero-area boxes."""
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("Bounding boxes must have positive width and height")
        return self


class VisibleElement(BaseModel):
    """Keep an element's description, label, value and location together."""

    model_config = ConfigDict(extra="forbid", strict=True)

    description: str
    label: str | None
    value: str | None
    bounding_box: BoundingBox | None


class ScreenObservation(BaseModel):
    """Describe only what one screenshot visibly supports, without decisions."""

    model_config = ConfigDict(extra="forbid", strict=True)

    page_description: str
    visible_elements: list[VisibleElement]
    possible_event: str | None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)

    @property
    def labels(self) -> list[str | None]:
        """Return labels in visible-element order, retaining unknowns."""
        return [element.label for element in self.visible_elements]

    @property
    def values(self) -> list[str | None]:
        """Return values in visible-element order, retaining unknowns."""
        return [element.value for element in self.visible_elements]

    @property
    def bounding_boxes(self) -> list[BoundingBox | None]:
        """Return locations in visible-element order, retaining unknowns."""
        return [element.bounding_box for element in self.visible_elements]
