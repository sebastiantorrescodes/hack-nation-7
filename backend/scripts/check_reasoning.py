"""Synthetic model check. Use a Free tier Google project or zero-price OpenRouter.
Never prints secrets or captured records.
"""
import asyncio
import argparse
import json
import time

import httpx

from app import api_llm


async def main(frame=False, image=False):
    provider, model = api_llm.configured_model()
    print("Configured provider:", provider)
    print("Configured model:", model)
    if provider == "openrouter":
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get("https://openrouter.ai/api/v1/models")
            print("Model catalog HTTP:", response.status_code)
            if response.status_code == 200:
                entry = next((m for m in response.json()["data"] if m["id"] == model), None)
                print("Model listed:", bool(entry))
                if entry:
                    print("Input modalities:", entry["architecture"].get("input_modalities"))
                    print("Structured outputs:", "structured_outputs" in entry.get("supported_parameters", []))
        except (httpx.RequestError, ValueError, KeyError):
            print("Model catalog unavailable.")
    started = time.monotonic()
    try:
        if image:
            import base64
            import io
            from PIL import Image, ImageDraw, ImageFont
            from app.routes.capture import screen_preview, ScreenPreviewBody
            # Generated fixture, not a real screenshot or record. Pillow is optional for this test only.
            picture = Image.new("RGB", (1000, 500), "white")
            draw = ImageDraw.Draw(picture)
            font = ImageFont.load_default(size=36)
            for y, text in [(40, "SYNTHETIC EXPENSE TEST"), (120, "Amount: 120"),
                            (200, "Receipt: Missing"), (280, "Status: on_hold")]:
                draw.text((40, y), text, font=font, fill="black")
            stream = io.BytesIO(); picture.save(stream, format="JPEG")
            result = await screen_preview(ScreenPreviewBody(image_base64=base64.b64encode(stream.getvalue()).decode()))
            values = json.dumps(result["fields"]).lower()
            passed = "120" in values and ("on_hold" in values or "on hold" in values)
            print("Synthetic image-only preview:", "PASS" if passed else "FAIL")
            print("Fields extracted:", len(result["fields"]))
            if not passed: raise SystemExit(1)
        elif frame:
            from app.routes.capture import FRAME_SYSTEM
            from app.llm import FRAME_SCHEMA
            result = await api_llm.structured(system=FRAME_SYSTEM, schema=FRAME_SCHEMA, max_tokens=4000, effort="low",
                content="Synthetic expense workflow. Previous screen: amount 120, receipt missing, status pending. "
                "Action: expert changed Status from pending to on_hold. Current page fields: amount 120, "
                "receipt missing, status on_hold. No expert explanation yet.")
            fields = json.loads(result["record_fields_json"])
            passed = fields.get("status") == "on_hold" and result["is_decision_point"] and bool(result["ask_why"])
            print("Synthetic frame inference:", "PASS" if passed else "FAIL")
            print("Fields extracted:", len(fields))
            print("Decision question generated:", bool(result["ask_why"]))
            if not passed: raise SystemExit(1)
        else:
            result = await api_llm.structured(
                system="Return only the required JSON. This is a synthetic connectivity test.",
                content="The synthetic receipt status is pending. Return ok=true.",
                schema={"type": "object", "properties": {"ok": {"type": "boolean"}},
                        "required": ["ok"], "additionalProperties": False}, max_tokens=2048, effort="low")
            print("Synthetic inference:", "PASS" if result == {"ok": True} else "FAIL")
            if result != {"ok": True}: raise SystemExit(1)
    except api_llm.ReasoningAPIError as exc:
        print("Synthetic inference:", str(exc))
        raise SystemExit(1) from None
    finally:
        print("Latency seconds:", round(time.monotonic() - started, 1))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame", action="store_true", help="Check the real screen-analysis schema using synthetic text.")
    parser.add_argument("--image", action="store_true", help="Check image-only screen preview with a generated fixture (requires Pillow).")
    args = parser.parse_args()
    asyncio.run(main(args.frame, args.image))
