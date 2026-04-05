"""Own file name: gui_rendering.py

Responsibility:
This module defines the Gradio rendering UI and the request handlers that read uploaded
reference images, call the image-generation backend, and upload source and generated image
artifacts to the configured remote directory.

Used by:
* (no direct callers found)

Pipelines:
- startup -> load_env -> configure_logging -> load_prompt_template
- ui -> get_demo -> blocks -> click -> handle_render -> request_render -> generate_image -> upload_and_share_file -> gallery

Invariants:
- `MODEL_OPTIONS` is derived from `MODEL_CATALOG` and defines the allowed model identifiers.
- `PROMPT_RENDERING` falls back to `""` when `prompt/prompt_rendering.txt` is missing.
- `request_render` does not import or require Gradio.
- `handle_render` returns a status string for both success and failure paths.

Out of scope:
- Image-generation backend implementation.
- Remote storage behavior beyond calling `upload_and_share_file`.
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
from helper.helper_config import configure_logging, get_env_flag, load_env
from helper.helper_llm_image import generate_image
import gradio as gr

PNG_REMOTE_DIR = "/Documents/Rendering"

load_env(dotenv_path=Path(__file__).parent / ".env")
logger = configure_logging("rendering")
_NEXTCLOUD_BACKUP_ENABLED = False

MODEL_CATALOG = {
    # Cost hints are embedded in labels to make quick UI comparisons possible.
    "gemini-3-pro-image-preview": "Nano-Banana-Pro",   # $120, $0.134 per 1K/2K image
    "gemini-3.1-flash-image-preview": "Nano-Banana-2",   # $60, $0.045 per 1K/2K image
    "gemini-2.5-flash-image": "Nano-Banana",   # $0.039 per image
    "gpt-image-1.5": "GPT-1.5",    # $32, $0.133 per image
    "gpt-image-1": "GPT-1",   # $40, $0.167 per image
    "gpt-image-1-mini": "GPT-1-Mini",   # $8.00, $0.036 per images
    "grok-imagine-image-pro": "Grok-image",   # $0.07 per images
    #"grok-2-image-1212": "Grok-2, $",   # $0.07 per images
    "stability-structure": "SD-Structure, $$",
    "stability-sketch": "SD-Sketch, $",
    "stability-core": "SD-Core, $",
    "stability-sd3": "SD-3, $",
    "stability-ultra": "SD-Ultra, $$$",
}

PROMPT_RENDERING_PATH = Path(__file__).parent / "prompt" / "prompt_rendering.txt"
MODEL_OPTIONS = list(MODEL_CATALOG)
MAX_BLEND_INPUTS = 14
RENDERING_REFERENCE_URL = "https://nextcloud.ampco.com.hk/index.php/s/GtNbJbGpr624iQW"

if PROMPT_RENDERING_PATH.exists():
    PROMPT_RENDERING = PROMPT_RENDERING_PATH.read_text("utf-8")
else:
    logger.error(
        "Prompt template not found at %s. Rendering requests will fail.", PROMPT_RENDERING_PATH
    )
    PROMPT_RENDERING = ""


def _supports_multi_image_blending(model_name: str) -> bool:
    """Purpose:
    Determine whether a model name is treated as supporting multi-image blending.

    Inputs:
    - model_name: Model identifier to inspect.

    Outputs:
    - `True` when the lowercased identifier starts with `gemini` and contains `image`;
      otherwise `False`.
    """
    lowered = model_name.lower()
    return lowered.startswith("gemini") and "image" in lowered


def _blend_prompt_suffix(image_count: int) -> str:
    """Purpose:
    Build the fixed prompt suffix used for multi-image blending requests.

    Inputs:
    - image_count: Number of uploaded reference images.

    Outputs:
    - Instruction text that requires the backend to incorporate all uploaded references.
    """
    return (
        "Blend all provided references into one cohesive photorealistic result. "
        f"Use each uploaded reference (1 to {image_count}) as a required source. "
        "If people are present, preserve identity consistency for up to 5 people. "
        "Keep defining object/material attributes from each reference and maintain coherent "
        "perspective, lighting direction, and shadows. Do not add extra people."
    )


def request_render(image_bytes_list: list[bytes] | None, model: str, prompt: str) -> bytes:
    """Purpose:
    Build the final prompt and request one generated image from `generate_image`.

    Inputs:
    - image_bytes_list: Optional uploaded source image bytes passed through to the backend.
    - model: Model identifier passed to the backend.
    - prompt: Additional prompt text appended to the template prompt.

    Outputs:
    - The first generated image as raw bytes.
    """
    system_prompt = PROMPT_RENDERING.strip()
    user_text = (prompt or "").strip()
    if system_prompt and user_text:
        final_prompt = f"{system_prompt}\n\n{user_text}"
    else:
        final_prompt = system_prompt or user_text
    if not final_prompt:
        raise RuntimeError("Rendering prompt is empty.")

    input_images = [item for item in (image_bytes_list or []) if item]
    if len(input_images) > 1:
        final_prompt = f"{final_prompt}\n\n{_blend_prompt_suffix(len(input_images))}"

    images = generate_image(
        model=model,
        prompt=final_prompt,
        size="1024x1024",
        n=1,
        image_bytes=input_images[0] if input_images else None,
        image_bytes_list=input_images or None,
    )

    if not images:
        raise ValueError("Image generation did not return any image bytes.")

    return images[0]


def handle_render(uploaded: str | list[str] | None, model: str | list[str] | None, prompt: str):
    """Purpose:
    Validate uploaded inputs, render one image per selected model, and upload source and
    generated artifacts.

    Inputs:
    - uploaded: Local file path or paths from Gradio's `File(type="filepath")`, or `None`.
    - model: Selected model id or model ids from the Gradio checkbox group, or `None`.
    - prompt: Additional prompt text.

    Outputs:
    - A tuple of `(rendered_images, status_message)` for the Gradio gallery and status box.
    - `rendered_images` is a list of `(PIL.Image.Image, caption)` tuples on success.
    - Successful completion messages include the fixed rendering reference URL.
    - Validation and failure paths return an empty list, or `None` on the file-read failure path,
      plus a user-facing status message.
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

    if isinstance(uploaded, str):
        uploaded_files = [uploaded]
    else:
        uploaded_files = [path for path in (uploaded or []) if path]

    if not uploaded_files:
        return [], "Upload at least one image before submitting."

    if len(uploaded_files) > MAX_BLEND_INPUTS:
        return [], f"Upload up to {MAX_BLEND_INPUTS} images."

    source_images: list[bytes] = []
    for uploaded_file in uploaded_files:
        try:
            with open(uploaded_file, "rb") as image_file:
                source_images.append(image_file.read())
        except Exception as exc:
            logger.exception("Unable to read the uploaded image.")
            return None, f"Failed to read the uploaded file: {exc}"
        if _NEXTCLOUD_BACKUP_ENABLED:
            upload_and_share_file(uploaded_file, PNG_REMOTE_DIR, share=False)

    if len(source_images) > 1 and any(_supports_multi_image_blending(item) for item in models):
        total_bytes = sum(len(item) for item in source_images)
        if total_bytes > 20 * 1024 * 1024:
            return [], (
                "Rendering failed: Gemini inline image payload exceeds 20MB. "
                "Reduce image count or file size."
            )

    try:
        rendered_images = []
        fallback_models = []
        source_stem = Path(uploaded_files[0]).stem
        blend_suffix = f"_blend{len(uploaded_files)}" if len(uploaded_files) > 1 else ""
        for model_name in models:
            rendered_bytes = request_render(source_images, model_name, prompt)
            rendered_image = Image.open(BytesIO(rendered_bytes))
            rendered_images.append((rendered_image, model_name))
            if len(uploaded_files) > 1 and not _supports_multi_image_blending(model_name):
                fallback_models.append(model_name)
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            generated_filename = f"{source_stem}{blend_suffix}_{model_name}_{ts}.png"
            if _NEXTCLOUD_BACKUP_ENABLED:
                upload_and_share_file(
                    rendered_bytes,
                    PNG_REMOTE_DIR,
                    share=False,
                    filename=generated_filename,
                )
        status = "Rendering complete."
        if len(models) > 1:
            status = f"Rendering complete for {len(models)} models."
        if fallback_models:
            status = (
                f"{status} {', '.join(fallback_models)} used the first uploaded image only."
            )
        if _NEXTCLOUD_BACKUP_ENABLED:
            status = f'{status} [ARCHIVED NEXRCLOUD FOLDER]({RENDERING_REFERENCE_URL})'
        return rendered_images, status
    except Exception as exc:
        logger.exception("Rendering failed.")
        return [], f"Rendering failed: {exc}"


@lru_cache(maxsize=1)
def get_demo() -> "gr.Blocks":
    """Purpose:
    Build and cache the Gradio `Blocks` interface wired to `handle_render`.

    Inputs:
    - None.

    Outputs:
    - A cached `gr.Blocks` instance.
    """
    import gradio as gr

    with gr.Blocks(title="Sketch-to-Rendering Studio", head=CLIPBOARD_POLYFILL) as demo:
        gr.Markdown("## Sketch-to-Rendering Studio")
        with gr.Row():
            upload_image = gr.File(
                label="Upload reference images",
                type="filepath",
                file_count="multiple",
                file_types=[".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"],
            )
            model_picker = gr.CheckboxGroup(
                label="Choose a rendering model",
                choices=[(MODEL_CATALOG.get(model, model), model) for model in MODEL_OPTIONS],
                value=[MODEL_OPTIONS[2]],
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
            status_message = gr.Markdown(
                value="Upload images, select one or more models, then press Generate Rendering."
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
