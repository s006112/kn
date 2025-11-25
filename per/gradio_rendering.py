from __future__ import annotations

import gradio as gr

from clipboard_polyfill import CLIPBOARD_POLYFILL
from core_rendering import MODEL_OPTIONS, PROMPT_RENDERING, handle_render


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
            choices=MODEL_OPTIONS,
            value=MODEL_OPTIONS[0],
        )
    prompt_editor = gr.Textbox(
        label="Rendering prompt (edit before submitting)",
        value=PROMPT_RENDERING,
        lines=10,
    )
    with gr.Row():
        generate_btn = gr.Button("Generate Rendering")
    with gr.Column():
        rendered_output = gr.Image(label="Generated rendering", interactive=False)
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
    demo.launch()
