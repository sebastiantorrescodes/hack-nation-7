"""Check the API contract and failures without using credentials or live requests."""

import json
import unittest
from unittest.mock import patch

import httpx

from app import api_llm
from app.agent.apprentice import process_event
from app.agent.schemas import CaptureContext, WorkMapState
from app.events.normalizer import normalize_browser_event


WAIT = {"action": "wait", "reason": "Need more evidence", "question": None,
        "step": None, "step_id": None}


class APITests(unittest.IsolatedAsyncioTestCase):
    """Mock HTTP boundaries while exercising the actual API adapter."""

    def setUp(self):
        self.env = patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key",
            "OPENROUTER_MODEL": api_llm.DEFAULT_MODEL}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.dotenv = patch.object(api_llm, "load_dotenv")
        self.dotenv.start()
        self.addCleanup(self.dotenv.stop)

    async def call(self, handler):
        return await api_llm.structured(system="Instructions", content="Observed facts",
            schema={"type": "object"}, transport=httpx.MockTransport(handler))

    async def test_free_routing_and_schema_request(self):
        def handler(request):
            self.assertEqual(str(request.url), api_llm.ENDPOINT)
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], api_llm.DEFAULT_MODEL)
            self.assertTrue(payload["provider"]["require_parameters"])
            self.assertTrue(all(price == 0 for price in payload["provider"]["max_price"].values()))
            self.assertEqual(payload["response_format"]["type"], "json_schema")
            self.assertNotIn("models", payload)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                "message": {"content": json.dumps(WAIT)}}]})
        self.assertEqual(await self.call(handler), WAIT)

    async def test_image_blocks_and_effort_use_openrouter_format(self):
        content = [{"type": "text", "text": "Page snapshot"},
                   {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}]
        def handler(request):
            payload = json.loads(request.content)
            self.assertEqual(payload["messages"][1]["content"], [
                {"type": "text", "text": "Page snapshot"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}])
            self.assertEqual(payload["reasoning"], {"effort": "high"})
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                "message": {"content": json.dumps(WAIT)}}]})
        result = await api_llm.structured(system="Instructions", content=content, schema={"type": "object"},
            effort="high", transport=httpx.MockTransport(handler))
        self.assertEqual(result, WAIT)

    async def test_missing_key_and_paid_model_fail_before_network(self):
        for values in [{"OPENROUTER_API_KEY": ""}, {"OPENROUTER_MODEL": "qwen/paid"}]:
            with patch.dict("os.environ", values), self.assertRaises(api_llm.ReasoningAPIError):
                await self.call(lambda request: self.fail("Network must not be called"))

    async def test_http_errors_hide_provider_body(self):
        for status in [401, 402, 429, 503]:
            with self.subTest(status=status), self.assertRaises(api_llm.ReasoningAPIError) as error:
                await self.call(lambda request: httpx.Response(status, text="PRIVATE PROVIDER CONTENT"))
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertIn(str(status), str(error.exception))

    async def test_invalid_refused_and_truncated_responses(self):
        bodies = [{}, {"choices": []}, {"choices": [{"finish_reason": "length",
            "message": {"content": json.dumps(WAIT)}}]},
            {"choices": [{"finish_reason": "stop", "message": {"refusal": "Refused"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]},
            {"choices": [{"finish_reason": "stop", "message": {"content": "invalid"}}]}]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(api_llm.ReasoningAPIError):
                await self.call(lambda request: httpx.Response(200, json=body))

    async def test_connection_failure(self):
        def handler(request):
            raise httpx.ConnectError("private diagnostic", request=request)
        with self.assertRaises(api_llm.ReasoningAPIError) as error:
            await self.call(handler)
        self.assertNotIn("private diagnostic", str(error.exception))

    async def test_default_agent_uses_api_and_rejects_arbitrary_action(self):
        event = normalize_browser_event({"event_id": "e1", "event_type": "click", "target": "Review"})
        state = WorkMapState(expert_name="Expert")
        with patch.object(api_llm, "structured", return_value=WAIT) as reasoning:
            result = await process_event(event, state, CaptureContext(expert_paused=True))
            self.assertEqual(result.action, "wait")
            reasoning.assert_awaited_once()
        with patch.object(api_llm, "structured", return_value={**WAIT, "action": "execute_shell"}):
            with self.assertRaises(ValueError):
                await process_event(event, state, CaptureContext(expert_paused=True))
        self.assertEqual(state.questions, [])
        self.assertEqual(state.steps, [])


if __name__ == "__main__":
    unittest.main()
