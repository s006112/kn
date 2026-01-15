from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.utils_config import configure_logging, load_env
from helper.utils_llm_image import generate_image

load_env(dotenv_path=Path(__file__).parent / ".env")
logger = configure_logging("rendering")

MODEL_OPTIONS = [
    "gemini-3-pro-image-preview",   # $120, $0.134 per 1K/2K image
    "gemini-2.5-flash-image",   # $0.039 per image
    "gpt-image-1.5",    # $32, $0.133 per image 
    "gpt-image-1-mini",   # $40, $0.167 per image
    "gpt-image-1",   # $8.00, $0.036 per image
    "stability-structure",
    "stability-sketch",
    "stability-core",
    "stability-sd3",
    "stability-ultra",
]

PROMPT_RENDERING_PATH = Path(__file__).parent / "prompt" / "prompt_rendering.txt"
if PROMPT_RENDERING_PATH.exists():
    PROMPT_RENDERING = PROMPT_RENDERING_PATH.read_text("utf-8")
else:
    logger.error(
        "Prompt template not found at %s. Rendering requests will fail.", PROMPT_RENDERING_PATH
    )
    PROMPT_RENDERING = ""


def _compose_prompt(system_prompt: str, user_text: str) -> str:
    system_prompt = system_prompt.strip()
    user_text = (user_text or "").strip()
    if system_prompt and user_text:
        return f"{system_prompt}\n\n{user_text}"
    return system_prompt or user_text


def request_render(image_bytes: bytes | None, model: str, prompt: str) -> bytes:
    """使用指定圖像模型產生渲染結果。

    - Stability 系列（stability-*）：
        - 如果提供 image_bytes，則走 image-to-image。
        - 如果未提供，則為 text-to-image。
    - OpenAI gpt-image-1：
        - 如果提供 image_bytes，走 image-to-image 編輯模式。
        - 如果未提供，則為 text-to-image。
    - 其他模型（Gemini 等）目前僅支援 text-to-image，會忽略 image_bytes。
    """
    final_prompt = _compose_prompt(PROMPT_RENDERING, prompt)
    if not final_prompt:
        raise RuntimeError("Rendering prompt is empty.")

    images = generate_image(
        model=model,
        prompt=final_prompt,
        size="1024x1024",
        n=1,
        image_bytes=image_bytes,   # 關鍵：不要再做 startswith("stability") 判斷
    )

    if not images:
        raise ValueError("Image generation did not return any image bytes.")

    return images[0]


def handle_render(uploaded: str | None, model: str, prompt: str):
    if model not in MODEL_OPTIONS:
        return None, "Select a valid model from the dropdown before submitting."

    sketch_bytes: bytes | None = None
    if uploaded:
        try:
            with open(uploaded, "rb") as image_file:
                sketch_bytes = image_file.read()
        except Exception as exc:
            logger.exception("Unable to read the uploaded image.")
            return None, f"Failed to read the uploaded file: {exc}"

    try:
        rendered_bytes = request_render(sketch_bytes, model, prompt)
        rendered_image = Image.open(BytesIO(rendered_bytes))
        return rendered_image, "Rendering complete."
    except Exception as exc:
        logger.exception("Rendering failed.")
        return None, f"Rendering failed: {exc}"


__all__ = ["MODEL_OPTIONS", "PROMPT_RENDERING", "handle_render", "request_render"]
