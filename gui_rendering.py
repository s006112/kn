"""Gradio GUI entrypoint for model-backed image generation and file upload.

Pipelines:
- import -> load_env -> configure_logging -> read_template -> build_ui
- submit -> validate_models -> read_upload -> render -> upload -> gallery -> status

Invariants:
- `MODEL_OPTIONS` defines the allowed model identifiers.
- `PROMPT_RENDERING` is `""` when the template file is missing.
- `demo` is constructed at import time.

Out of scope:
- Any model validation beyond membership in `MODEL_OPTIONS`.
- Persistence or caching of generated images.
- Serving configuration beyond `demo.launch(...)` in `__main__`.
"""

from __future__ import annotations

import sys
from datetime import datetime
from io import BytesIO
from pathlib import Path

import gradio as gr
from PIL import Image

from clipboard_polyfill import CLIPBOARD_POLYFILL

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.utils_config import configure_logging, load_env
from helper.utils_llm_image import generate_image
from helper.helper_nextcloud import upload_and_share_file

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
    - user_text: User-provided prompt, may be empty.

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
        image_bytes=image_bytes,  # Why: `generate_image` resolves backend routing.
    )

    if not images:
        raise ValueError("Image generation did not return any image bytes.")

    return images[0]


def handle_render(uploaded: str | None, model: str | list[str] | None, prompt: str):
    """Purpose:
    Validate input, read an uploaded file if present, and return a rendered image.

    Inputs:
    - uploaded: Filepath for the uploaded image, or None.
    - model: Model identifier string selected by the user.
    - prompt: User prompt text to combine with the template.

    Outputs:
    - (list[(PIL.Image.Image, model_name)], status message string) on success.
    - ([], status message string) on validation failure or render failure.
    - (None, status message string) when the uploaded file cannot be read.

    Side effects:
    - Reads the uploaded file from disk when provided.
    - Logs exceptions during file read and rendering.

    Failure modes:
    - Returns ([], error message) if no models are selected or any model is invalid.
    - Returns (None, error message) if the uploaded file path cannot be read.
    - Returns ([], error message) if rendering fails for any reason, including when
      `uploaded` is None and the generated filename cannot be derived.
    """
    if isinstance(model, str):
        models = [model]
    else:
        models = list(model or [])

    if not models:
        return [], "Select at least one model before submitting."

    invalid_models = [item for item in models if item not in MODEL_OPTIONS]
    if invalid_models:
        return [], "Select valid models from the list before submitting."

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
        rendered_images = []
        for model_name in models:
            rendered_bytes = request_render(sketch_bytes, model_name, prompt)
            rendered_image = Image.open(BytesIO(rendered_bytes))
            rendered_images.append((rendered_image, model_name))
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            generated_filename = f"{Path(uploaded).stem}_{model_name}_{ts}.png"
            upload_and_share_file(
                rendered_bytes,
                PNG_REMOTE_DIR,
                share=False,
                filename=generated_filename,
            )
        status = "Rendering complete."
        if len(models) > 1:
            status = f"Rendering complete for {len(models)} models."
        return rendered_images, status
    except Exception as exc:
        logger.exception("Rendering failed.")
        return [], f"Rendering failed: {exc}"


DISPLAY_NAMES = {
    "gemini-3-pro-image-preview": "Nano Banana Pro, $$$",
    "gemini-2.5-flash-image": "Nano Banana, $",
    "gpt-image-1.5": "GPT-Image 1.5, $$",
    "gpt-image-1-mini": "GPT-Image 1.0, $$$",
    "gpt-image-1": "GPT-Image 1, $",
    "stability-ultra": "Stable Diffusion Ultra, $$$$",
    "stability-core": "Stable Diffusion Core, $$",
    "stability-sd3": "Stable Diffusion 3, $$",
    "stability-sketch": "Stable Diffusion Sketch, $$",
    "stability-structure": "Stable Diffusion Structure, $$$",
}

with gr.Blocks(title="Sketch-to-Rendering Studio", head=CLIPBOARD_POLYFILL) as demo:
    gr.Markdown("## Sketch-to-Rendering Studio")
    with gr.Row():
        upload_image = gr.File(
            label="Upload sketch photo or CAD drawing (images only)",
            type="filepath",
            file_types=[".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"],
        )
        model_picker = gr.CheckboxGroup(
            label="Choose a rendering model",
            choices=[(DISPLAY_NAMES.get(model, model), model) for model in MODEL_OPTIONS],
            value=[MODEL_OPTIONS[1], MODEL_OPTIONS[2], MODEL_OPTIONS[4]],
        )
    prompt_editor = gr.Textbox(
        label="Additional prompt (optional)",
        value="",
        placeholder="Add any extra rendering instructions here.",
        lines=6,
    )
    with gr.Row():
        generate_btn = gr.Button("Generate Rendering")
    with gr.Column():
        rendered_output = gr.Gallery(
            label="Generated renderings",
            interactive=False,
            columns=2,
            format="jpeg",
        )
        status_message = gr.Textbox(
            label="Status",
            value="Upload an image, select one or more models, then press Generate Rendering.",
            interactive=False,
        )
    generate_btn.click(
        fn=handle_render,
        inputs=[upload_image, model_picker, prompt_editor],
        outputs=[rendered_output, status_message],
    )


__all__ = [
    "MODEL_OPTIONS",
    "PROMPT_RENDERING",
    "handle_render",
    "request_render",
    "demo",
]


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7760)
