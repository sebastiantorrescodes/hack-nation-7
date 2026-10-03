# This file tests observation validation and the vision adapter without model weights.

from contextlib import nullcontext
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from pydantic import ValidationError

from app.vision.qwen_provider import QwenVisionProvider, VisionOutputError, parse_observation
from app.vision.schemas import ScreenObservation


def screen_payload() -> dict:
    """Return a synthetic billing screen observation with no expert knowledge."""
    return {
        "page_description": "Billing form",
        "visible_elements": [{
            "description": "Modifier input", "label": "Modifier", "value": "25",
            "bounding_box": {"x_min": 0.5, "y_min": 0.3, "x_max": 0.7, "y_max": 0.5},
        }],
        "possible_event": None, "confidence": 0.9,
    }


class ObservationTests(unittest.TestCase):
    """Check useful screen facts and malformed model responses."""

    def test_json_and_fenced_json_keep_element_associations(self):
        """Keep each label, value and box associated in both output formats."""
        raw = json.dumps(screen_payload())
        for text in [raw, f"```json\n{raw}\n```"]:
            with self.subTest(text=text):
                result = parse_observation(text)
                self.assertEqual(result.labels, ["Modifier"])
                self.assertEqual(result.values, ["25"])
                self.assertEqual(result.bounding_boxes[0].x_min, 0.5)

    def test_unknowns_and_empty_screen(self):
        """Permit unknown fields and an empty screen without inventing facts."""
        payload = screen_payload()
        payload["visible_elements"][0].update(label=None, value=None, bounding_box=None)
        self.assertEqual(parse_observation(json.dumps(payload)).values, [None])
        payload["visible_elements"] = []
        self.assertEqual(parse_observation(json.dumps(payload)).visible_elements, [])

    def test_bad_coordinates_and_confidence_are_rejected(self):
        """Reject invalid geometry, non-finite scores and wrong types."""
        for changes in [{"x_min": -0.1}, {"x_max": 1.1}, {"x_min": 0.7}, {"y_max": 0.2}]:
            payload = screen_payload()
            payload["visible_elements"][0]["bounding_box"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ScreenObservation.model_validate(payload)
        for confidence in [-1, 1.1, float("nan"), float("inf"), "0.9", True]:
            payload = screen_payload()
            payload["confidence"] = confidence
            with self.subTest(confidence=confidence), self.assertRaises(ValidationError):
                ScreenObservation.model_validate(payload)

    def test_invalid_output_fails_without_echoing_screen_contents(self):
        """Reject incomplete JSON, prose, missing fields and unrelated outputs."""
        payload = screen_payload()
        payload["ask_expert"] = "private screen contents"
        for raw in ["private screen contents", "{}", "{", json.dumps(payload), "prefix " + json.dumps(screen_payload())]:
            with self.subTest(raw=raw), self.assertRaises(VisionOutputError) as caught:
                parse_observation(raw)
            self.assertNotIn("private screen contents", str(caught.exception))


class ProviderTests(unittest.TestCase):
    """Exercise loading, prompt trimming and inference with fake model APIs."""

    def setUp(self):
        """Provide an isolated local folder and fake optional inference packages."""
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.model = Mock(device="cpu")
        self.model.to.return_value = self.model
        self.model.generate.return_value = [[11, 12, 31, 32]]
        self.processor = Mock()

        class Inputs(dict):
            """Represent the small subset of BatchFeature used by the adapter."""

            def to(self, device):
                """Track the chosen inference device without real tensors."""
                self.device = device
                return self

        self.inputs = Inputs(input_ids=[[11, 12]], token_type_ids=[[0, 0]])
        self.processor.apply_chat_template.return_value = self.inputs
        self.processor.batch_decode.return_value = [json.dumps(screen_payload())]
        self.transformers = SimpleNamespace(
            AutoConfig=Mock(), AutoModelForImageTextToText=Mock(), AutoProcessor=Mock()
        )
        self.transformers.AutoConfig.from_pretrained.return_value = SimpleNamespace(model_type="qwen3_vl")
        self.transformers.AutoProcessor.from_pretrained.return_value = self.processor
        self.transformers.AutoModelForImageTextToText.from_pretrained.return_value = self.model
        self.torch = SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False),
            backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: False)),
            float32="float32", float16="float16", inference_mode=nullcontext,
        )
        modules = patch.dict("sys.modules", {"torch": self.torch, "transformers": self.transformers})
        modules.start()
        self.addCleanup(modules.stop)

    def test_adapter_loads_once_locally_and_trims_prompt_tokens(self):
        """Pass image data to Qwen, decode only new tokens, and reuse weights."""
        provider = QwenVisionProvider(self.folder.name)
        image = Image.new("RGBA", (100, 80))
        for _ in range(2):
            result = provider.analyze_screenshot(image)
            self.assertEqual(result.values, ["25"])
        for loader in [self.transformers.AutoConfig, self.transformers.AutoProcessor, self.transformers.AutoModelForImageTextToText]:
            loader.from_pretrained.assert_called_once()
            kwargs = loader.from_pretrained.call_args.kwargs
            self.assertTrue(kwargs["local_files_only"])
            self.assertFalse(kwargs["trust_remote_code"])
        self.processor.batch_decode.assert_called_with(
            [[31, 32]], skip_special_tokens=True, clean_up_tokenization_spaces=False
        )
        messages = self.processor.apply_chat_template.call_args.args[0]
        self.assertEqual(messages[0]["content"][0]["image"].mode, "RGB")
        self.assertNotIn("token_type_ids", self.model.generate.call_args.kwargs)
        self.assertFalse(self.model.generate.call_args.kwargs["do_sample"])
        self.assertEqual(self.model.generate.call_args.kwargs["max_new_tokens"], 2048)

    def test_text_only_model_is_rejected(self):
        """Reject a Qwen language model before loading its weights."""
        self.transformers.AutoConfig.from_pretrained.return_value.model_type = "qwen3"
        with self.assertRaisesRegex(ValueError, "text-only"):
            QwenVisionProvider(self.folder.name).analyze_screenshot(Image.new("RGB", (10, 10)))
        self.transformers.AutoModelForImageTextToText.from_pretrained.assert_not_called()

    def test_invalid_image_never_loads_model(self):
        """Validate input before expensive model initialization."""
        with self.assertRaises(ValueError):
            QwenVisionProvider(self.folder.name).analyze_screenshot("not an image")
        self.transformers.AutoConfig.from_pretrained.assert_not_called()

    def test_bad_model_output_is_not_replaced_with_mock_success(self):
        """Propagate output validation failures from actual inference flow."""
        self.processor.batch_decode.return_value = ["not JSON"]
        with self.assertRaises(VisionOutputError):
            QwenVisionProvider(self.folder.name).analyze_screenshot(Image.new("RGB", (10, 10)))


if __name__ == "__main__":
    unittest.main()
