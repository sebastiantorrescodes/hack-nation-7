# This file defines the single operation that a vision provider must support.

from abc import ABC, abstractmethod

from PIL import Image

from .schemas import ScreenObservation


class VisionProvider(ABC):
    """Convert an in-memory screenshot to validated visible screen facts."""

    @abstractmethod
    def analyze_screenshot(self, image: Image.Image) -> ScreenObservation:
        """Inspect one screenshot without asking questions or saving data."""
        raise NotImplementedError
