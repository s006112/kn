"""This module handles:

* Gradio UI construction and event wiring for the rendering workflow.

The processing pipeline:
1. Build the UI: upload, model selector, prompt input, output panel.
2. On click, call core_rendering.handle_render.
3. Display the returned image and status message.

Invariants:
* Model choices are derived from core_rendering.MODEL_OPTIONS.
* Display names fall back to raw model ids when not mapped.

Out of scope:
* Rendering logic, prompt composition, or image generation.
* Model list management beyond display mapping.
"""

from __future__ import annotations

import gradio as gr

from clipboard_polyfill import CLIPBOARD_POLYFILL
from core_rendering import MODEL_OPTIONS, handle_render

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
        model_picker = gr.Dropdown(
            label="Choose a rendering model",
            choices=[(DISPLAY_NAMES.get(model, model), model) for model in MODEL_OPTIONS],
            value=MODEL_OPTIONS[0],
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
        rendered_output = gr.Image(
            label="Generated rendering",
            interactive=False,
            format="jpeg",  # ensure downloads default to JPG
        )
        status_message = gr.Textbox(
            label="Status",
            value="Upload an image, select a model, then press Generate Rendering.",
            interactive=False,
        )
    generate_btn.click(
        fn=handle_render,
        inputs=[upload_image, model_picker, prompt_editor],
        outputs=[rendered_output, status_message],
    )


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7760)
