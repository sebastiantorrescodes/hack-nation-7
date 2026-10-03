# This file independently tests local vision on one supplied screenshot.

import argparse
import os

from PIL import Image

from app.vision.qwen_provider import QwenVisionProvider


def main() -> None:
    """Print an observation from a local model and an explicitly chosen image."""
    parser = argparse.ArgumentParser(description="Analyze one screenshot using local Qwen3-VL")
    parser.add_argument("screenshot", help="Path to a PNG or JPEG screenshot")
    parser.add_argument("--model-path", default=os.getenv("QWEN_MODEL_PATH"))
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "mps", "cuda"])
    args = parser.parse_args()
    if not args.model_path:
        parser.error("Set QWEN_MODEL_PATH or pass --model-path with your local Hugging Face model folder")
    try:
        provider = QwenVisionProvider(args.model_path, device=args.device)
        with Image.open(args.screenshot) as image:
            observation = provider.analyze_screenshot(image)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Vision test failed: {exc}\n")
    print(observation.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
