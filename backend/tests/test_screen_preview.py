import base64
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import main
from app.routes import capture
from app.api_llm import ReasoningAPIError

JPEG = base64.b64encode(b"\xff\xd8synthetic-fixture\xff\xd9").decode()


class ScreenPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_reads_image_without_any_session_or_evidence_write(self):
        result={"summary":"Synthetic expense", "record_fields_json":'{"amount":120}',"uncertainties":[]}
        with patch.object(capture,"structured",AsyncMock(return_value=result)) as model, \
             patch.object(capture,"_get",AsyncMock()) as lookup, \
             patch.object(capture.store,"create_capture_session",AsyncMock()) as create, \
             patch.object(capture.store,"ingest_capture",AsyncMock()) as ingest:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url="http://localhost") as client:
                response=await client.post("/api/capture/screen-preview",json={"image_base64":JPEG})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["fields"],{"amount":120})
        self.assertEqual(model.call_args.kwargs["content"][1]["source"]["data"],JPEG)
        lookup.assert_not_awaited(); create.assert_not_awaited(); ingest.assert_not_awaited()

    async def test_invalid_or_absent_picture_does_not_call_model(self):
        with patch.object(capture,"structured",AsyncMock()) as model:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url="http://localhost") as client:
                for body in [{},{"image_base64":"not-an-image"},{"image_base64":base64.b64encode(b"not JPEG").decode()}]:
                    response=await client.post("/api/capture/screen-preview",json=body)
                    self.assertEqual(response.status_code,422)
        model.assert_not_awaited()

    async def test_invalid_model_fields_fail_without_creating_evidence(self):
        with patch.object(capture,"structured",AsyncMock(return_value={"summary":"Screen","record_fields_json":"[]","uncertainties":[]})):
            with self.assertRaises(ReasoningAPIError):
                await capture.screen_preview(capture.ScreenPreviewBody(image_base64=JPEG))
