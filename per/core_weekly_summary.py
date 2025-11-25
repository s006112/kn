from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils_config import load_prompt_text
from utils_llm import call_llm

LLM_MODEL = "gemini-2.5-pro"
#LLM_MODEL = "sonar"
#LLM_MODEL = "gemini-2.0-flash"
#LLM_MODEL = "gemini-3-pro"
#LLM_MODEL = "gpt-4.1-mini"
#LLM_MODEL = "sonar, gemini-2.5-flash, gemini-3-pro"

def _append_to_weekly_log(base_dir: Path, source_text: str, summary_text: str) -> None:
    """Append the raw input and generated summary to weekly.log."""

    log_path = base_dir / "weekly.log"
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


def generate_weekly_summary(user_text: str, base_dir: Path) -> str:
    """
    Core weekly summary generation:
      - Load prompt template
      - Call the configured LLM
      - Append request/response to weekly.log
    """
    if not user_text or not user_text.strip():
        return "Error: No text provided."

    prompt_text = load_prompt_text(base_dir, "prompt_w.txt")
    if not prompt_text:
        return "Error: Failed to load prompt_w.txt"

    try:
        weekly_summary = call_llm(
            model=LLM_MODEL,
            system_prompt=prompt_text,
            user_text=user_text,
        )
    except Exception as exc:
        return f"Error querying LLM: {exc}"
    _append_to_weekly_log(base_dir, user_text, weekly_summary)
    return weekly_summary


__all__ = ["generate_weekly_summary"]
