"""Gradio GUI entrypoint for LLM-backed weekly summary generation.

"""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from pathlib import Path
import gradio as gr

from clipboard_polyfill import CLIPBOARD_POLYFILL
from helper.utils_config import configure_logging, load_env, load_prompt_text
from helper.utils_llm import call_llm

# LLM_MODEL = "gemini-2.5-pro"
#LLM_MODEL = "sonar"
# LLM_MODEL = "gemini-2.0-flash"
# LLM_MODEL = "gemini-3-pro-preview"
LLM_MODEL = "gpt-4.1-mini"
# LLM_MODEL = "sonar, gemini-2.5-flash, gemini-3-pro-preview"

load_env()
logger = configure_logging("weekly")


def _append_to_weekly_log(base_dir: Path, source_text: str, summary_text: str) -> None:
    """Append the raw input and generated summary to weekly.log."""

    log_path = base_dir / "log" / "weekly.log"
    timestamp = datetime.now().isoformat(timespec="seconds")
    entry_lines = [
        "",
        f"=== Submission at {timestamp} ===",
        "[Input]",
        source_text.rstrip(),
        "",
        "[Weekly Summary]",
        summary_text.rstrip(),
        "",
    ]
    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write("\n".join(entry_lines))
    except Exception:
        # Logging errors for weekly log should not break the main flow.
        pass


def generate_weekly_summary(user_text: str, base_dir: Path, model: str) -> str:
    """
    Core weekly summary generation:
      - Load prompt template
      - Call the configured LLM
      - Append request/response to weekly.log
    """
    if not user_text or not user_text.strip():
        return "Error: No text provided."

    prompt_dir = base_dir / "prompt"
    prompt_text = load_prompt_text(prompt_dir, "prompt_w.txt")
    if not prompt_text:
        return "Error: Failed to load prompt_w.txt"

    try:
        weekly_summary = call_llm(
            model=model,
            system_prompt=prompt_text,
            user_text=user_text,
        )
    except Exception as exc:
        return f"Error querying LLM: {exc}"

    _append_to_weekly_log(base_dir, user_text, weekly_summary)
    return weekly_summary


def handle_upload(user_text: str) -> str:
    """Gradio callback for weekly summary generation."""
    base_dir = Path(__file__).parent
    return generate_weekly_summary(user_text, base_dir, model=LLM_MODEL)


@lru_cache(maxsize=1)
def get_demo() -> "gradio.Blocks":
    with gr.Blocks(title="Weekly Summary", head=CLIPBOARD_POLYFILL) as demo:
        with gr.Row():
            inp = gr.Textbox(
                lines=5,
                placeholder="Paste the content you want to analyse...",
            )
        btn = gr.Button("Submit")

        weekly_summary_box = gr.Textbox(
            label="Weekly Summary", lines=14, show_copy_button=True
        )

        btn.click(
            handle_upload,
            inputs=inp,
            outputs=weekly_summary_box,
        )

    return demo


__all__ = ["LLM_MODEL", "generate_weekly_summary", "handle_upload", "get_demo"]


if __name__ == "__main__":
    get_demo().launch(server_name="0.0.0.0", server_port=1986)

