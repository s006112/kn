from __future__ import annotations

import base64
import logging
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils_config import configure_logging, load_env
from utils_llm import get_openai_client

load_env(dotenv_path=Path(__file__).parent / ".env")
logger = configure_logging("rendering")

MODEL_OPTIONS = ["gpt-image-1"]
REQUEST_TIMEOUT = 60

PROMPT_RENDERING = Path(__file__).with_name("Prompt_rendering.txt").read_text("utf-8")


def _fetch_image_bytes_from_url(url: str) -> bytes:
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.content


def request_perplexity_render(image_bytes: bytes, model: str) -> bytes:
    # Currently Perplexity does not expose an image render API.
    # Use the configured image-capable LLM (for example gpt-image-1)
    # to generate a photorealistic rendering based on the textual instructions.
    try:
        client = get_openai_client()
        result = client.images.generate(
            model=model,
            prompt=PROMPT_RENDERING,
            size="1024x1024",
            n=1,
        )
    except Exception as exc:
        raise RuntimeError(f"Image generation request failed: {exc}") from exc

    if not result.data:
        raise ValueError("Image generation did not return any data.")

    image_info = result.data[0]
    if getattr(image_info, "b64_json", None):
        try:
            return base64.b64decode(image_info.b64_json)
        except (base64.binascii.Error, ValueError) as exc:
            raise ValueError("Image generation returned an invalid base64 payload.") from exc

    if getattr(image_info, "url", None):
        return _fetch_image_bytes_from_url(image_info.url)

    raise ValueError("Image generation response did not contain usable image data.")


def handle_render(uploaded: str | None, model: str) -> tuple[Image.Image | None, str]:
    if not uploaded:
        return None, "Please upload a sketch or CAD drawing before generating."

    if model not in MODEL_OPTIONS:
        return None, "Select a valid model from the dropdown before submitting."

    try:
        with open(uploaded, "rb") as image_file:
            sketch_bytes = image_file.read()
    except Exception as exc:
        logger.exception("Unable to read the uploaded image.")
        return None, f"Failed to read the uploaded file: {exc}"

    try:
        rendered_bytes = request_perplexity_render(sketch_bytes, model)
        rendered_image = Image.open(BytesIO(rendered_bytes))
        return rendered_image, "Rendering complete."
    except Exception as exc:
        logger.exception("Rendering failed.")
        return None, f"Rendering failed: {exc}"


__all__ = ["MODEL_OPTIONS", "handle_render", "request_perplexity_render"]
