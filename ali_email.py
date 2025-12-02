from __future__ import annotations

import time
from pathlib import Path

from utils_config import configure_logging, get_env_int  # type: ignore :contentReference[oaicite:1]{index=1}
from ali_fetch import fetch_new_messages  # type: ignore :contentReference[oaicite:2]{index=2}
from ali_llm import generate_reply  # type: ignore :contentReference[oaicite:3]{index=3}
from ali_send import send_reply  # type: ignore
from utils_imap_types import EmailMessage, SendResult

# sonar, sonar-pro, sonar-reasoning, sonar-reasoning-pro
# gemini-2.0-flash, gemini-2.5-flash, gemini-2.5-pro, gemini-3-pro-preview, 
# gpt-5-mini, gpt-5-nano, gpt-4.1-mini, gpt-4.1-nano, gpt-4o-mini, o1-mini, o3-mini, o4-mini, codex-mini-latest
# gpt-5.1, gpt-5, gpt-5-chat-latest, gpt-4.1, gpt-4o, o1, o3,
LLM_MODEL = "sonar"
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompt" / "prompt_ali_system.txt"


def run_once() -> None:
    # Set up logger for this pipeline run
    logger = configure_logging("ali_pipeline")

    # Fixed maximum messages per polling cycle
    max_messages = 10

    # Fetch new messages from IMAP
    messages: list[EmailMessage] = fetch_new_messages(max_messages=max_messages)
    if not messages:
        logger.info("No new messages to process.")
        return

    logger.info("Processing %d messages.", len(messages))

    # Process each message: generate reply and send
    for msg in messages:
        try:
            logger.info("Processing uid=%s subject=%s", msg.uid, msg.subject)

            reply_body = generate_reply(msg, system_prompt_path=SYSTEM_PROMPT_PATH, model=LLM_MODEL)
            result: SendResult = send_reply(msg, reply_body)

            if result.ok:
                logger.info("Processed uid=%s successfully.", msg.uid)
            else:
                logger.error(
                    "Send failed for uid=%s: %s",
                    msg.uid,
                    result.error_message or "unknown error",
                )
        except Exception as exc:  # pragma: no cover - defensive
            # Catch-all to avoid crashing the whole loop on one bad email
            logger.error("Unhandled error processing uid=%s: %s", msg.uid, exc)

    logger.info("run_once finished.")


if __name__ == "__main__":
    # Read polling interval (minutes) from environment or default
    interval_minutes = get_env_int("ALI_POLL_INTERVAL_MINUTES", 1)
    # Main loop: run pipeline, then sleep before next poll
    while True:
        run_once()
        time.sleep(interval_minutes * 60)
