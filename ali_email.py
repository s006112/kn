#!/usr/bin/env python3
"""
ali_pipeline.py

單次輪詢流程：
- 建立 AliStateStore
- 透過 ali_email_fetcher 抓 UNSEEN + 未成功處理的信
- 對每封信：
    - ali_llm_responder.generate_reply → 產生回信正文
    - ali_email_sender.send_reply → 寄出
    - 依結果 mark_success / mark_failure
"""

from __future__ import annotations

import os

from utils_config import load_env, configure_logging  # type: ignore :contentReference[oaicite:1]{index=1}
from ali_email_fetcher import fetch_new_messages  # type: ignore :contentReference[oaicite:2]{index=2}
from ali_llm_responder import generate_reply  # type: ignore :contentReference[oaicite:3]{index=3}
from ali_email_sender import send_reply  # type: ignore
from ali_state_store import AliStateStore  # type: ignore


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def run_once() -> None:
    load_env()
    logger = configure_logging("ali_pipeline")

    state = AliStateStore()
    max_messages = _get_env_int("ALI_MAX_MESSAGES", 10)

    messages = fetch_new_messages(state, max_messages=max_messages)
    if not messages:
        logger.info("No new messages to process.")
        return

    logger.info("Processing %d messages.", len(messages))

    for msg in messages:
        try:
            logger.info("Processing uid=%s subject=%s", msg.uid, msg.subject)

            reply_body = generate_reply(msg)
            result = send_reply(msg, reply_body)

            if result.ok:
                state.mark_success(msg.uid)
                logger.info("Processed uid=%s successfully.", msg.uid)
            else:
                state.mark_failure(msg.uid, result.error_message or "send failed")
                logger.error(
                    "Send failed for uid=%s: %s",
                    msg.uid,
                    result.error_message or "unknown error",
                )
        except Exception as exc:  # pragma: no cover - defensive
            state.mark_failure(msg.uid, str(exc))
            logger.error("Unhandled error processing uid=%s: %s", msg.uid, exc)

    logger.info("run_once finished.")


if __name__ == "__main__":
    run_once()
