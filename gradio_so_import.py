from __future__ import annotations

import os

import gradio as gr

from core_so_import import run_so_import as core_run_so_import
from clipboard_polyfill import CLIPBOARD_POLYFILL

_SHOW_PO_TEXTBOXES = os.getenv("DEBUG_TEXTBOXES", "false").strip().lower() == "true"
LLM_MODEL = "gpt-5-mini"


def handle_upload(file_path: str, salesperson: str) -> tuple[str, str, str, dict]:
    """
    Gradio callback
    - Read PDF path, extract text
    - Query the configured LLM for PO extraction details
    - Inject the manually provided salesperson name into the response

    Returns:
      po_response_text (str), pdf_parsing_text (str), import_log (str), order_link_update (dict)
    """

    hidden_link_update = gr.update(value="", visible=False)
    result = core_run_so_import(file_path, salesperson, model=LLM_MODEL)

    link_update = hidden_link_update
    if result.sale_order_link_url:
        url = result.sale_order_link_url
        link_update = gr.update(value=f"[{url}]({url})", visible=True)

    return result.po_response_text, result.pdf_parsing_text, result.import_log, link_update


with gr.Blocks(title="SO importer", head=CLIPBOARD_POLYFILL) as demo:
    # Minimal visible controls: Upload, Submit, PO response
    with gr.Row():
        inp = gr.File(label="Upload PDF File", file_types=[".pdf"], type="filepath")
        salesperson_input = gr.Textbox(label="Sales person", lines=1, placeholder="Enter sales person name")
    btn = gr.Button("Submit")

    order_link = gr.Markdown("", visible=False)

    import_log_box = gr.Textbox(label="Import Log", lines=2, interactive=False)

    po_response_box = gr.Textbox(label="PO response", lines=14, show_copy_button=True, visible=_SHOW_PO_TEXTBOXES)

    pdf_parsing_box = gr.Textbox(
        label="PDF parsing",
        lines=10,
        show_copy_button=True,
        elem_id="pdf_parsing_box",
        visible=_SHOW_PO_TEXTBOXES,
    )

    # Wire outputs: PO response (visible), raw PDF parsing text (hidden but copyable), import log, and sale link
    btn.click(
        handle_upload,
        inputs=[inp, salesperson_input],
        outputs=[po_response_box, pdf_parsing_box, import_log_box, order_link],
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7960)
