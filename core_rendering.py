"""This module handles:

* Rendering orchestration for model-backed image generation.

The processing pipeline:
1. Load environment variables and configure logging on import.
2. Read the prompt template from prompt/prompt_rendering.txt.
3. Compose the final prompt from template and user input.
4. Call the image generator with a selected model and optional image bytes.
5. Return the first generated image or an error message.

Invariants:
* Allowed models are defined by MODEL_OPTIONS.
* PROMPT_RENDERING may be empty if the template file is missing.

Out of scope:
* UI construction or event wiring.
* Model validation beyond membership in MODEL_OPTIONS.
* Persistence or caching of rendered images.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.utils_config import configure_logging, load_env
from helper.utils_llm_image import generate_image
from helper.utils_nextcloud import upload_and_share_file

PNG_REMOTE_DIR = "/Documents/Rendering"

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
    """Purpose:
    Compose the final prompt from a system template and user input.

    Inputs:
    - system_prompt: Template string, may be empty.
    - user_text: User-provided prompt, may be empty or None.

    Outputs:
    - Combined prompt string with whitespace trimmed.

    Side effects:
    - None.

    Failure modes:
    - None.
    """
    system_prompt = system_prompt.strip()
    user_text = (user_text or "").strip()
    if system_prompt and user_text:
        return f"{system_prompt}\n\n{user_text}"
    return system_prompt or user_text


def request_render(image_bytes: bytes | None, model: str, prompt: str) -> bytes:
    """Purpose:
    Generate a single rendered image using the configured image backend.

    Inputs:
    - image_bytes: Optional source image bytes for image-to-image/edit paths.
    - model: Model identifier string passed through to generate_image.
    - prompt: User prompt text to combine with the template.

    Outputs:
    - Raw image bytes for the first generated image.

    Side effects:
    - Calls generate_image with the resolved prompt and parameters.

    Failure modes:
    - RuntimeError if the composed prompt is empty.
    - ValueError if generate_image returns no images.
    - Propagates exceptions from generate_image.
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
    """Purpose:
    Validate input, read an uploaded file if present, and return a rendered image.

    Inputs:
    - uploaded: Filepath for the uploaded image, or None.
    - model: Model identifier string selected by the user.
    - prompt: User prompt text to combine with the template.

    Outputs:
    - (PIL.Image.Image | None, status message string).

    Side effects:
    - Reads the uploaded file from disk when provided.
    - Logs exceptions during file read and rendering.

    Failure modes:
    - Returns (None, error message) if model is invalid, file read fails,
      or rendering raises an exception.
    """
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
        upload_and_share_file(uploaded, PNG_REMOTE_DIR, share=False)

    try:
        rendered_bytes = request_render(sketch_bytes, model, prompt)
        rendered_image = Image.open(BytesIO(rendered_bytes))
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        generated_filename = f"{Path(uploaded).name}_{ts}.png"
        temp_path = Path(tempfile.gettempdir()) / generated_filename
        try:
            temp_path.write_bytes(rendered_bytes)
            upload_and_share_file(str(temp_path), PNG_REMOTE_DIR, share=False)
        except Exception:
            pass
        temp_path.unlink(missing_ok=True)
        return rendered_image, "Rendering complete."
    except Exception as exc:
        logger.exception("Rendering failed.")
        return None, f"Rendering failed: {exc}"


__all__ = ["MODEL_OPTIONS", "PROMPT_RENDERING", "handle_render", "request_render"]
