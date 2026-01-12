from __future__ import annotations

from pathlib import Path

import gradio as gr

from clipboard_polyfill import CLIPBOARD_POLYFILL
from core_weekly_summary import generate_weekly_summary
from helper.utils_config import configure_logging, load_env

#LLM_MODEL = "gemini-2.5-pro"
#LLM_MODEL = "sonar"
#LLM_MODEL = "gemini-2.0-flash"
#LLM_MODEL = "gemini-3-pro-preview"
LLM_MODEL = "gpt-5.1"
#LLM_MODEL = "sonar, gemini-2.5-flash, gemini-3-pro-preview"

load_env()
logger = configure_logging("weekly")


def handle_upload(user_text: str) -> str:
    """
    Gradio callback
    - Accept manual text input
    - Delegate to core_weekly_summary for LLM-based summary generation and logging
    """
    base_dir = Path(__file__).parent
    return generate_weekly_summary(user_text, base_dir, model=LLM_MODEL)


with gr.Blocks(title="Weekly Summary", head=CLIPBOARD_POLYFILL) as demo:
    # Minimal visible controls: Text input, Submit, Weekly summary
    with gr.Row():
        inp = gr.Textbox(
            label="Paste Text",
            lines=5,
            placeholder="Paste the content you want to analyse...",
        )
    btn = gr.Button("Submit")

    weekly_summary_box = gr.Textbox(label="Weekly Summary", lines=14, show_copy_button=True)

    # Wire outputs: Weekly summary response from the configured LLM
    btn.click(
        handle_upload,
        inputs=inp,
        outputs=weekly_summary_box,
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=1986)
