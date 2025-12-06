from __future__ import annotations

import os

import gradio as gr

from helper.utils_cie1931 import (
    CIE_CONTAINER_ID,
    CIE_DF_ID,
    CIE_PNG_NAME_ID,
    CIE_PNG_UPLOAD_ID,
    get_canvas_html,
    get_drawing_javascript,
)
from core_per_report import handle_upload as core_handle_upload, upload_cie_png
from clipboard_polyfill import CLIPBOARD_POLYFILL

#LLM_MODEL = "gemini-2.5-pro"
#LLM_MODEL = "sonar"
#LLM_MODEL = "gemini-2.0-flash"
#LLM_MODEL = "gemini-3-pro"
LLM_MODEL = "gpt-4.1-mini"
#LLM_MODEL = "sonar, gemini-2.5-flash, gemini-3-pro"

DEBUG_TEXTBOXES = os.getenv("DEBUG_TEXTBOXES", "false").strip().lower() == "true"


def handle_upload(file_path: str):
    return core_handle_upload(file_path, model=LLM_MODEL)


# ----------------------------
# UI
# ----------------------------
with gr.Blocks(title="Photometric extraction", head=CLIPBOARD_POLYFILL) as demo:
    # Minimal visible controls: Upload, Submit, Summary, CIE canvas
    with gr.Row():
        inp = gr.File(label="Upload PDF File", file_types=[".pdf"], type="filepath")
    btn = gr.Button("Submit")

    combined_summary_box = gr.Textbox(label="Summary", lines=14, show_copy_button=True)

    # CIE chart
    gr.HTML(get_canvas_html(), elem_id=CIE_CONTAINER_ID)

    # Expose raw PDF text and parsed x,y table; hide them via CSS unless debugging is enabled.
    original_text_box = gr.Textbox(
        label="Raw PDF text",
        lines=10,
        show_copy_button=True,
        elem_id="original_text_box",
    )

    cct_xy_box = gr.Dataframe(
        label="CIE x,y (parsed from Spectral Parameters)",
        headers=["参数", "x", "y"],
        interactive=False,
        elem_id=CIE_DF_ID,
    )

    # Hidden textbox used by JS to signal a PNG data URL to backend
    cie_png_upload_box = gr.Textbox(
        label="CIE PNG upload payload",
        lines=1,
        elem_id=CIE_PNG_UPLOAD_ID,
    )

    # Hidden textbox to pass the planned PNG filename from backend to frontend
    cie_png_name_box = gr.Textbox(
        label="CIE PNG filename",
        lines=1,
        elem_id=CIE_PNG_NAME_ID,
    )

    hidden_rules = [
        "#cie_png_upload { display: none !important; }",
        "#cie_png_name { display: none !important; }",
    ]
    if not DEBUG_TEXTBOXES:
        hidden_rules.extend(
            [
                "#cct_xy_df { display: none !important; }",
                "#original_text_box { display: none !important; }",
            ]
        )

    gr.HTML(
        "<style>\n  " + "\n  ".join(hidden_rules) + "\n</style>"
    )

    # Wire outputs: summary, parsed table, raw text, and planned PNG filename
    btn.click(
        handle_upload,
        inputs=inp,
        outputs=[combined_summary_box, cct_xy_box, original_text_box, cie_png_name_box],
    )

    # Load JS for CIE canvas drawing
    demo.load(fn=lambda: None, inputs=[], outputs=[], js=get_drawing_javascript())

    # Wire hidden upload bridge: when JS writes JSON into the hidden textbox, upload PNG
    cie_png_upload_box.change(upload_cie_png, inputs=[cie_png_upload_box], outputs=[combined_summary_box])

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
