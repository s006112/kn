"""Gradio GUI entrypoint for model-backed image generation using an uploaded sketch.

Used by:
* (no direct callers found)

Pipelines:
- startup -> load_env -> configure_logging -> load_prompt_template
- ui -> get_demo -> blocks -> click -> handle_render -> request_render -> generate_image -> upload_and_share_file -> gallery

Invariants:
- `MODEL_OPTIONS` is the source of truth for allowed model identifiers.
- `PROMPT_RENDERING` is `""` when `prompt/prompt_rendering.txt` is missing.
- `request_render` and `handle_render` do not import or require Gradio.

Out of scope:
- Validation beyond membership in `MODEL_OPTIONS`.
- Storage backends beyond calling `upload_and_share_file`.
- Launch configuration beyond the hard-coded `__main__` invocation.
"""


from __future__ import annotations
from datetime import datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from PIL import Image
from clipboard_polyfill import CLIPBOARD_POLYFILL
from helper.helper_nextcloud import upload_and_share_file
from helper.utils_config import configure_logging, load_env
from helper.utils_llm_image import generate_image
import gradio as gr

PNG_REMOTE_DIR = "/Documents/Rendering"

load_env(dotenv_path=Path(__file__).parent / ".env")
logger = configure_logging("rendering")

MODEL_CATALOG = {
    # Cost hints are embedded in labels to make quick UI comparisons possible.
    "gemini-3-pro-image-preview": "Nano Banana Pro, $$$",
    "gemini-2.5-flash-image": "Nano Banana, $",
    "gpt-image-1.5": "GPT-Image 1.5, $$",
    "gpt-image-1-mini": "GPT-Image 1.0, $$$",
    "gpt-image-1": "GPT-Image 1, $",
    "stability-structure": "Stable Diffusion Structure, $$$",
    "stability-sketch": "Stable Diffusion Sketch, $$",
    "stability-core": "Stable Diffusion Core, $$",
    "stability-sd3": "Stable Diffusion 3, $$",
    "stability-ultra": "Stable Diffusion Ultra, $$$$",
}

PROMPT_RENDERING_PATH = Path(__file__).parent / "prompt" / "prompt_rendering.txt"
MODEL_OPTIONS = list(MODEL_CATALOG)

if PROMPT_RENDERING_PATH.exists():
    PROMPT_RENDERING = PROMPT_RENDERING_PATH.read_text("utf-8")
else:
    logger.error(
        "Prompt template not found at %s. Rendering requests will fail.", PROMPT_RENDERING_PATH
    )
    PROMPT_RENDERING = ""


def request_render(image_bytes: bytes | None, model: str, prompt: str) -> bytes:
    """Purpose:
    Generate a single image via `generate_image`, optionally conditioning on an uploaded sketch.

    Inputs:
    - image_bytes: Optional source image bytes passed through to the backend.
    - model: Model identifier passed to the backend.
    - prompt: Additional prompt text appended to the template prompt.

    Outputs:
    - Rendered image bytes (the first element returned by `generate_image`).

    Side effects:
    - None.

    Failure modes:
    - Raises `RuntimeError` if the combined prompt is empty.
    - Raises `ValueError` if the backend returns no images.
    - Propagates exceptions from `generate_image`.
    """
    system_prompt = PROMPT_RENDERING.strip()
    user_text = (prompt or "").strip()
    if system_prompt and user_text:
        final_prompt = f"{system_prompt}\n\n{user_text}"
    else:
        final_prompt = system_prompt or user_text
    if not final_prompt:
        raise RuntimeError("Rendering prompt is empty.")

    images = generate_image(
        model=model,
        prompt=final_prompt,
        size="1024x1024",
        n=1,
        image_bytes=image_bytes,
    )

    if not images:
        raise ValueError("Image generation did not return any image bytes.")

    return images[0]


def handle_render(uploaded: str | None, model: str | list[str] | None, prompt: str):
    """Purpose:
    Validate inputs, read an uploaded file, render one image per selected model, and upload artifacts.

    Inputs:
    - uploaded: Local filepath from Gradio's `File(type="filepath")`, or `None`.
    - model: A single model id, a list of model ids, or `None` (from Gradio CheckboxGroup).
    - prompt: Additional prompt text.

    Outputs:
    - `(rendered_images, status_message)` where `rendered_images` is a list of
      `(PIL.Image.Image, caption)` tuples for display in a Gradio Gallery.
    - On upload/read failures, returns an empty list (or `None` on one legacy path) plus an error string.

    Side effects:
    - Reads the uploaded file from disk when `uploaded` is provided.
    - Calls `upload_and_share_file` for the uploaded file and each rendered image.
    - Logs exceptions.

    Failure modes:
    - Returns user-facing validation errors when no models are selected or models are invalid.
    - Returns `([], "Rendering failed: ...")` on rendering/upload failures after logging.
    - Rendering without an uploaded filepath fails because output filenames derive from `uploaded`.
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


@lru_cache(maxsize=1)
def get_demo() -> "gr.Blocks":
    """Purpose:
    Build and cache the Gradio `Blocks` demo wiring inputs to `handle_render`.

    Inputs:
    - None.

    Outputs:
    - A `gr.Blocks` instance.

    Side effects:
    - Imports Gradio inside the function.
    - Memoizes the constructed UI via `lru_cache`.

    Failure modes:
    - Propagates exceptions raised during Gradio component construction.
    """
    import gradio as gr

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
                choices=[(MODEL_CATALOG.get(model, model), model) for model in MODEL_OPTIONS],
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
                rows=1,
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
    return demo


__all__ = [
    "MODEL_OPTIONS",
    "PROMPT_RENDERING",
    "handle_render",
    "request_render",
    "get_demo",
]


if __name__ == "__main__":
    get_demo().launch(server_name="0.0.0.0", server_port=7760)
