"""Weekly summary Gradio UI.

Responsibility:
This module provides a Gradio UI that takes freeform text, calls an LLM to
produce a weekly summary using `prompt/prompt_w.txt`, and appends the request
and response to `log/weekly.log`.

Used by:
* (no direct callers found)

Pipelines:
- user_text -> prompt_load -> llm_call -> log_append -> summary_text

Invariants:
- Import-time side effects call `load_env()` and configure the `weekly` logger.
- Empty input returns an error string without calling the LLM.
- LLM call failures return an error string; they do not raise.
- Weekly log append failures are suppressed and do not affect the response.

Out of scope:
- Prompt authoring or prompt validation.
- Multi-user session management or access control.
- File upload parsing; this UI accepts text only.
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
LLM_MODEL = "gpt-5-mini"
# LLM_MODEL = "sonar, gemini-2.5-flash, gemini-3-pro-preview"

load_env()
logger = configure_logging("weekly")


def _append_to_weekly_log(base_dir: Path, source_text: str, summary_text: str) -> None:
    """Append one request/response entry to `log/weekly.log`.

    Purpose:
    Append `source_text` and `summary_text` with an ISO timestamp header.

    Inputs:
    - base_dir: Module base directory used to locate `log/weekly.log`.
    - source_text: Raw user-provided text.
    - summary_text: LLM-generated weekly summary text.

    Outputs:
    - None.

    Side effects:
    - Appends to `base_dir/log/weekly.log` if the file is writable.

    Failure modes:
    - Any exception during file IO is suppressed.
    """

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
    """Generate a weekly summary for input text using an LLM.

    Purpose:
    Load `prompt/prompt_w.txt`, call the LLM, and append the exchange to
    `log/weekly.log`.

    Inputs:
    - user_text: Source text to summarize.
    - base_dir: Directory containing `prompt/` and `log/` subdirectories.
    - model: Model identifier forwarded to `call_llm`.

    Outputs:
    - A weekly summary string, or an error string prefixed with `Error:`.

    Side effects:
    - Reads `base_dir/prompt/prompt_w.txt`.
    - Appends to `base_dir/log/weekly.log` on successful LLM calls.

    Failure modes:
    - Returns `Error: No text provided.` when input is empty/whitespace.
    - Returns `Error: Failed to load prompt_w.txt` when the prompt is empty.
    - Returns `Error querying LLM: ...` when `call_llm` raises.
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
    """Handle Gradio submit events by generating a weekly summary.

    Purpose:
    Resolve the module base directory and call `generate_weekly_summary` with
    the configured `LLM_MODEL`.

    Inputs:
    - user_text: Source text entered in the UI textbox.

    Outputs:
    - A weekly summary string, or an error string prefixed with `Error:`.

    Side effects:
    - Delegated to `generate_weekly_summary`.

    Failure modes:
    - Delegated to `generate_weekly_summary`.
    """
    base_dir = Path(__file__).parent
    return generate_weekly_summary(user_text, base_dir, model=LLM_MODEL)


@lru_cache(maxsize=1)
def get_demo() -> "gr.Blocks":
    """Create and cache the Gradio Blocks UI for weekly summary generation.

    Purpose:
    Construct the Gradio layout and wire the submit button to `handle_upload`.

    Inputs:
    - None.

    Outputs:
    - A `gradio.Blocks` instance.

    Side effects:
    - None beyond allocating Gradio component objects.

    Failure modes:
    - Propagates exceptions raised by Gradio component construction.
    """
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
