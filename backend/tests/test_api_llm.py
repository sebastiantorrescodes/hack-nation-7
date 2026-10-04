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

    async def test_gemini_key_selects_google_with_images_schema_and_thinking(self):
        content = [{"type": "text", "text": "Synthetic screen"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}}]
        def handler(request):
            self.assertEqual(str(request.url), api_llm.GEMINI_ENDPOINT)
            self.assertEqual(request.headers["Authorization"], "Bearer synthetic-google-key")
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], api_llm.DEFAULT_GEMINI_MODEL)
            self.assertEqual(payload["reasoning_effort"], "low")
            self.assertEqual(payload["response_format"]["json_schema"]["schema"], {"type": "object"})
            self.assertEqual(payload["messages"][1]["content"][1]["image_url"]["url"], "data:image/jpeg;base64,AAAA")
            self.assertNotIn("provider", payload)
            self.assertNotIn("reasoning", payload)
            self.assertNotIn("test-key", request.content.decode())
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
                "message": {"content": json.dumps(WAIT)}}]})
        with patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic-google-key"}):
            result = await api_llm.structured(system="Synthetic", content=content, schema={"type": "object"},
                transport=httpx.MockTransport(handler))
        self.assertEqual(result, WAIT)

    async def test_explicit_provider_wins_even_with_both_keys_configured(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic-google-key", "LLM_PROVIDER": "openrouter"}):
            self.assertEqual(api_llm.configured_model(), ("openrouter", api_llm.DEFAULT_MODEL))
            await self.test_free_routing_and_schema_request()

    async def test_google_failure_never_falls_back_to_openrouter_or_prints_credentials(self):
        for status in [400, 401, 403, 404, 429, 503]:
            calls = []
            def handler(request):
                calls.append(str(request.url))
                return httpx.Response(status, text="PRIVATE MODEL RESPONSE synthetic-google-key")
            with patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic-google-key"}), self.assertRaises(api_llm.ReasoningAPIError) as failure:
                await self.call(handler)
            self.assertEqual(calls, [api_llm.GEMINI_ENDPOINT])
            self.assertIn(f"Gemini HTTP {status}", str(failure.exception))
            self.assertNotIn("PRIVATE", str(failure.exception))
            self.assertNotIn("synthetic-google-key", str(failure.exception))

    async def test_unlisted_google_models_invalid_provider_and_missing_google_key_fail_locally(self):
        for values in [{"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": ""},
                {"LLM_PROVIDER": "gemini", "GEMINI_MODEL": "gemini-pro"}, {"LLM_PROVIDER": "arbitrary"}]:
            with patch.dict("os.environ", values), self.assertRaises(api_llm.ReasoningAPIError):
                await self.call(lambda request: self.fail("Invalid configuration must not send a request"))

    async def test_google_json_is_validated_with_the_same_evidence_schema(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "synthetic-google-key"}), self.assertRaises(api_llm.ReasoningAPIError):
            await api_llm.structured(system="Synthetic", content="Synthetic",
                schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [
                    {"finish_reason": "stop", "message": {"content": '{"ok": "not a boolean"}'}}]})))

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

    async def test_live_reasoning_deadline_is_shorter_than_a_whole_session_build(self):
        for effort, deadline in [("low", 45), ("high", 180)]:
            def handler(request):
                self.assertEqual(request.extensions["timeout"]["read"], deadline)
                self.assertEqual(request.extensions["timeout"]["connect"], 10)
                raise httpx.ReadTimeout("Private provider timeout", request=request)
            with self.subTest(effort=effort), self.assertRaises(api_llm.ReasoningAPIError) as failure:
                await api_llm.structured(system="Synthetic", content="Synthetic", schema={"type": "object"},
                    effort=effort, transport=httpx.MockTransport(handler))
            self.assertIn("Captured facts are retained", str(failure.exception))
            self.assertNotIn("Private provider", str(failure.exception))

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
