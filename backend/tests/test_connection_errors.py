"""Connection outages return useful JSON without exposing provider requests."""

import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import main
from app.routes import voice


class ConnectionErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_failure_is_503_json_and_does_not_retry_a_write(self):
        request = httpx.Request("GET", "https://database.test/rest/v1/workflows?private=value")
        error = httpx.ConnectError("Private provider message", request=request)
        with patch.object(main, "SUPABASE_URL", "https://database.test"), \
             patch.object(main.store, "get_workflow", AsyncMock(side_effect=error)) as lookup, \
             patch.object(main.store, "create_capture_session", AsyncMock()) as create:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                response = await client.post("/api/capture/sessions", json={"workflow_id": "test", "expert_name": "Test expert"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("database request", response.json()["detail"])
        self.assertNotIn("database.test", response.text)
        self.assertNotIn("Private provider", response.text)
        self.assertNotIn("private=value", response.text)
        lookup.assert_awaited_once()
        create.assert_not_awaited()

    async def test_voice_network_failure_is_not_reported_as_bad_credentials(self):
        request = httpx.Request("GET", "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url")
        error = httpx.ConnectTimeout("Private transport details", request=request)
        with patch.object(voice, "ELEVENLABS_API_KEY", "test-only"), \
             patch.object(voice, "ELEVENLABS_AGENT_ID", "test-only"), \
             patch.object(voice.httpx.AsyncClient, "get", AsyncMock(side_effect=error)):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                response = await client.request("GET", "/api/voice/signed-url")
        self.assertEqual(response.status_code, 503)
        self.assertIn("voice service", response.json()["detail"])
        self.assertNotIn("Private transport", response.text)

    async def test_unknown_or_unattached_request_has_a_safe_fallback(self):
        response = await main.connection_error(None, httpx.ConnectError("Private details"))
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"required service", response.body)
        self.assertNotIn(b"Private details", response.body)


if __name__ == "__main__":
    unittest.main()
