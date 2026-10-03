# This file tests event normalization using browser messages and synthetic vision facts.

import copy
import unittest

from pydantic import ValidationError

from app.events.normalizer import normalize_browser_event, normalize_vision_observation
from app.events.schemas import ObservedEvent
from app.vision.schemas import ScreenObservation


def vision_observation() -> ScreenObservation:
    """Describe two visible fields, including one with unreadable content."""
    return ScreenObservation.model_validate({
        "page_description": "Billing",
        "visible_elements": [
            {"description": "Modifier input", "label": "Modifier", "value": "25",
             "bounding_box": {"x_min": 0.5, "y_min": 0.3, "x_max": 0.7, "y_max": 0.5}},
            {"description": "Unlabeled input", "label": None, "value": None, "bounding_box": None},
        ],
        "possible_event": None,
        "confidence": 0.65,
    })


class BrowserEventTests(unittest.TestCase):
    """Verify direct actions preserve exact data and reject invalid inputs."""

    def test_structured_field_change_preserves_values_and_metadata(self):
        """Normalize the example without modifying the original input."""
        payload = {
            "event_id": "browser-1", "timestamp_ms": 1750000000123,
            "event_type": "field_change", "target": "Modifier",
            "old_value": "", "new_value": "25", "page": "Billing",
            "x_norm": 0.62, "y_norm": 0.41, "confidence": 0.96,
        }
        original = copy.deepcopy(payload)
        event = normalize_browser_event(payload)
        self.assertEqual(event.model_dump(exclude={"source", "source_event_id", "description", "bounding_box"}), payload)
        self.assertEqual(event.source, "browser")
        self.assertEqual(payload, original)

    def test_supported_browser_types_and_unknown_locations(self):
        """Preserve clicks, navigation and submission attempts with no coordinates."""
        for kind in ["click", "navigation", "submit_attempt"]:
            with self.subTest(kind=kind):
                event = normalize_browser_event({"event_type": kind, "page": "/billing"})
                self.assertEqual(event.event_type, kind)
                self.assertEqual(event.confidence, 1.0)
                self.assertIsNone(event.x_norm)
                self.assertIsNone(event.y_norm)

    def test_current_extension_field_change_does_not_invent_old_value(self):
        """Keep the extension's frame and millisecond timestamp."""
        event = normalize_browser_event({
            "type": "ui-action", "text": 'changed "Modifier" to "25"',
            "frame": "/interface/billing", "t": 1750000000123,
        })
        self.assertEqual(event.event_type, "field_change")
        self.assertEqual(event.target, "Modifier")
        self.assertEqual(event.new_value, "25")
        self.assertIsNone(event.old_value)
        self.assertEqual(event.page, "/interface/billing")
        self.assertEqual(event.timestamp_ms, 1750000000123)

    def test_current_extension_click_and_save_attempt(self):
        """Distinguish a Save click from the extension's actual save-attempt message."""
        clicked = normalize_browser_event({"type": "ui-action", "text": 'clicked "Save"'})
        submitted = normalize_browser_event({"type": "save-attempt"})
        self.assertEqual(clicked.event_type, "click")
        self.assertEqual(clicked.target, "Save")
        self.assertEqual(submitted.event_type, "submit_attempt")
        self.assertIsNone(submitted.target)

    def test_clearing_a_field_preserves_empty_string(self):
        """Treat an explicitly empty value differently from an unknown value."""
        event = normalize_browser_event({"type": "ui-action", "text": 'changed "Modifier" to ""'})
        self.assertEqual(event.new_value, "")

    def test_unknown_or_ambiguous_events_are_rejected(self):
        """Reject unsupported types, prose and legacy text with embedded quotes."""
        for payload in [
            {"event_type": "screen_observation"}, {"event_type": "scroll"},
            {"event_type": []}, {"event_type": {}},
            {"type": "ui-action", "text": 'changed "Field" to "a "quote""'},
            {"type": "ui-action", "text": "something changed"},
            {"type": "ui-action", "text": None},
            {"type": "save-attempt", "unknown": True},
            {"event_type": "click", "source": "vision"},
        ]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_browser_event(payload)

    def test_invalid_fields_coordinates_scores_and_timestamps(self):
        """Reject incomplete changes, invalid locations and corrupt capture metadata."""
        for fields in [
            {"event_type": "field_change", "new_value": "25"},
            {"event_type": "field_change", "target": "Modifier"},
            {"x_norm": 0.5}, {"x_norm": -0.1, "y_norm": 0.2},
            {"x_norm": 0.5, "y_norm": float("nan")},
            {"confidence": 1.1}, {"confidence": float("inf")},
            {"confidence": "0.9"}, {"timestamp_ms": -1}, {"timestamp_ms": 1.5},
            {"ask_expert": "Why?"},
        ]:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                normalize_browser_event({"event_type": "click", **fields})


class VisionEventTests(unittest.TestCase):
    """Verify screenshot facts remain observations with their original uncertainty."""

    def test_element_values_centers_and_provenance(self):
        """Retain each element's facts and the parent observation identifier."""
        observation = vision_observation()
        before = observation.model_dump()
        events = normalize_vision_observation(
            observation, page="/billing", observation_id="frame-1", timestamp_ms=1234
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].target, "Modifier")
        self.assertEqual(events[0].new_value, "25")
        self.assertAlmostEqual(events[0].x_norm, 0.6)
        self.assertAlmostEqual(events[0].y_norm, 0.4)
        self.assertEqual(events[0].bounding_box, observation.visible_elements[0].bounding_box)
        self.assertIsNone(events[1].new_value)
        self.assertIsNone(events[1].x_norm)
        self.assertIsNone(events[1].y_norm)
        self.assertNotEqual(events[0].event_id, events[1].event_id)
        for event in events:
            self.assertEqual(event.source, "vision")
            self.assertEqual(event.source_event_id, "frame-1")
            self.assertEqual(event.timestamp_ms, 1234)
            self.assertEqual(event.page, "/billing")
            self.assertEqual(event.confidence, 0.65)
            self.assertEqual(event.event_type, "screen_observation")
            self.assertIsNone(event.old_value)
        self.assertEqual(observation.model_dump(), before)

    def test_possible_event_does_not_become_a_verified_action(self):
        """Keep a model's visible-status description separate from DOM actions."""
        observation = vision_observation()
        observation.possible_event = "Saved successfully"
        events = normalize_vision_observation(observation)
        self.assertEqual(events[-1].description, "Saved successfully")
        self.assertEqual(events[-1].event_type, "screen_observation")
        self.assertEqual(events[-1].page, "Billing")
        self.assertIsNone(events[-1].target)

    def test_empty_screen_still_retains_page_observation(self):
        """Preserve an empty screen's description without making up elements."""
        observation = vision_observation()
        observation.visible_elements = []
        events = normalize_vision_observation(observation)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].description, "Billing")
        self.assertIsNone(events[0].target)
        self.assertIsNone(events[0].new_value)

    def test_common_event_round_trips_as_json(self):
        """Both sources serialize through the same validated event schema."""
        events = [normalize_browser_event({"event_type": "navigation", "page": "/billing"})]
        events += normalize_vision_observation(vision_observation())
        for event in events:
            self.assertEqual(ObservedEvent.model_validate_json(event.model_dump_json()), event)


if __name__ == "__main__":
    unittest.main()
