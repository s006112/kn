from __future__ import annotations

import logging
import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils_config import configure_logging, load_env
from utils_llm import generate_image

load_env(dotenv_path=Path(__file__).parent / ".env")
logger = configure_logging("rendering")

MODEL_OPTIONS = [
    "gemini-2.5-flash-image",
    "gemini-3-pro-image-preview",
    "gpt-image-1-mini",
    "gpt-image-1",
]

PROMPT_RENDERING_PATH = Path(__file__).with_name("Prompt_rendering.txt")
PROMPT_RENDERING = (
    PROMPT_RENDERING_PATH.read_text("utf-8") if PROMPT_RENDERING_PATH.exists() else ""
)


def request_perplexity_render(image_bytes: bytes, model: str) -> bytes:
    """
    使用指定圖像模型（OpenAI 或 Gemini）產生渲染結果。

    目前 Gemini 官方 image API 不支援真正的 image-to-image，
    所以這裡只把 PROMPT_RENDERING 丟給模型產圖。
    image_bytes 暫時保留以便未來擴充使用。
    """
    if not PROMPT_RENDERING:
        raise RuntimeError("Prompt_rendering.txt not found or empty.")

    images = generate_image(
        model=model,
        prompt=PROMPT_RENDERING,
        size="1024x1024",
        n=1,
    )

    if not images:
        raise ValueError("Image generation did not return any image bytes.")

    return images[0]


def handle_render(uploaded: str | None, model: str):
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
