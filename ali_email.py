#!/usr/bin/env python3
"""
ali_email.py

SYSTEM INVARIANTS (NON-NEGOTIABLE)

1. No Autonomous Action
   The system MUST NOT send any message to customers or third parties autonomously.
   All generated content is INTERNAL-ONLY unless a human explicitly copies and sends it.

2. Silence Means Termination
   If the email-sender does NOT reply with any non-empty content,
   the system MUST treat the review as REJECTED and MUST NOT continue processing.

3. Any Reply Is an Override
   Any NON-EMPTY reply from the email-sender MUST be interpreted as OVERRIDE instructions
   and MUST trigger a regenerated INTERNAL review using that reply as hard constraints.

4. FORWARD-ONLY INPUT MODEL (CRITICAL)
   The system ONLY accepts emails that are FORWARDED by a human reviewer.

   - The reviewer MUST forward the original email to ALI.
   - The email's From address MUST belong to the reviewer.
   - Emails sent on behalf of customers (e.g. CRM-generated emails,
     rewritten From headers, delegated senders) are intentionally REJECTED.

   Rationale:
   This invariant guarantees that ALI NEVER replies directly to customers,
   and that ALL outbound content is explicitly mediated by a human reviewer.


CALL FLOW (HIGH LEVEL)

pipeline_run()
  -> _phase1_new_messages()
       (Inbound UNSEEN emails, NON-review threads)
       -> ali_fetch.fetch_new_messages()
       -> ali_llm.generate_review_package()          # v1 rewrite
       -> ali_llm.render_review()
       -> _send_internal_review()
       -> ali_send.send_reply()

  -> _phase2_sender_replies()
       (UNSEEN replies to prior [ALI:vN] review threads)
       -> ali_fetch.fetch_sender_replies()
       -> ali_mail_parse.extract_sender_override()
            (sender-written override instructions only)
       -> ali_mail_parse.extract_last_review_state()
            (canonical LAST review version + draft from quoted history)
       -> ali_llm.generate_review_package(
              previous_draft = state.draft,
              edit_version   = state.version + 1
          )
       -> _send_internal_review(review_version = state.version + 1)
       -> ali_send.send_reply()
"""

from __future__ import annotations

import re
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from helper.utils_config import configure_logging, get_env_int  # type: ignore
from helper.utils_imap_types import EmailMessage, SendResult
from helper.utils_imap_ops import mark_imap_message_seen  # type: ignore

from ali_fetch import fetch_new_messages, fetch_sender_replies  # type: ignore
from ali_llm import generate_review_package, render_review  # type: ignore
from ali_send import send_reply  # type: ignore
from ali_mail_parse import (
    REVIEW_SUBJECT_MARKER,
    REVIEW_SUBJECT_PATTERN,
    extract_last_review_state,
    extract_sender_override,
)

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

LLM_MODEL = "gpt-4.1-mini"
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompt" / "prompt_ali_system.txt"

_HKT_ZONE = ZoneInfo("Asia/Hong_Kong")
_DAY_START = dt_time(9, 0)
_DAY_END = dt_time(18, 0)


def _default_poll_interval_minutes(now: datetime | None = None) -> int:
    """Return the default polling interval (in minutes)."""
    return 1


def _build_review_subject(subject: str, version: int) -> str:
    """Build outbound review subject with review marker appended."""
    marker = REVIEW_SUBJECT_MARKER.replace("X", str(version))
    cleaned = REVIEW_SUBJECT_PATTERN.sub("", subject or "")
    cleaned = re.sub(r"^(?:\s*re:\s*)+", "", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split())
    return f"{cleaned} {marker}".strip() if cleaned else marker


def _is_review_subject(subject: str) -> bool:
    """True if subject belongs to an ALI review thread."""
    return bool(REVIEW_SUBJECT_PATTERN.search(subject or ""))


def _send_internal_review(
    original: EmailMessage,
    review_body: str,
    *,
    logger,
    subject_override: str | None = None,
    review_version: int = 1,
) -> None:
    """Send internal review back to reviewer only."""
    reviewer = original.from_addr
    if not reviewer:
        raise RuntimeError("Missing reviewer (msg.from_addr is empty)")

    base_subject = subject_override if subject_override is not None else (original.subject or "")
    subject = _build_review_subject(base_subject, review_version)

    review_msg = EmailMessage(
        uid=original.uid,
        message_id=original.message_id,
        from_addr=reviewer,
        to_addrs=[reviewer],
        cc_addrs=[],
        subject=subject,
        body_text=original.body_text,
        raw_bytes=original.raw_bytes,
    )

    result: SendResult = send_reply(review_msg, review_body)

    if result.ok:
        logger.info("Internal review sent to %s (uid=%s)", reviewer, original.uid)
    else:
        logger.error(
            "Send failed for uid=%s: %s",
            original.uid,
            result.error_message or "unknown error",
        )


# -----------------------------------------------------------------------------
# Phase 1
# -----------------------------------------------------------------------------

def _phase1_new_messages(*, logger) -> None:
    """Process brand-new inbound messages into initial v1 drafts."""
    messages = fetch_new_messages(max_messages=2)
    if not messages:
        logger.info("No new messages to process.")
        return

    for msg in messages:
        try:
            logger.info("Processing new email uid=%s subject=%s", msg.uid, msg.subject)

            if _is_review_subject(msg.subject or ""):
                logger.info("Skipping review-thread message uid=%s", msg.uid)
                continue

            review_obj = generate_review_package(
                msg,
                system_prompt_path=SYSTEM_PROMPT_PATH,
                model=LLM_MODEL,
            )
            review_body = render_review(review_obj)

            _send_internal_review(msg, review_body, logger=logger)
            mark_imap_message_seen(msg.uid, logger=logger)

        except Exception as exc:
            logger.exception("Unhandled error processing uid=%s", msg.uid)


# -----------------------------------------------------------------------------
# Phase 2
# -----------------------------------------------------------------------------

def _phase2_sender_replies(*, logger) -> None:
    """Process sender replies as explicit override instructions."""
    sender_replies = fetch_sender_replies()
    if not sender_replies:
        logger.info("No sender replies to process.")
        return

    for reply_msg in sender_replies:
        try:
            logger.info(
                "Processing sender reply uid=%s subject=%s",
                reply_msg.uid,
                reply_msg.subject,
            )

            override_instructions = extract_sender_override(reply_msg.body_text or "")
            if not override_instructions:
                logger.info("Empty reply detected; treated as REJECT.")
                continue

            state = extract_last_review_state(reply_msg)

            override_input = EmailMessage(
                uid=reply_msg.uid,
                message_id=reply_msg.message_id,
                from_addr=reply_msg.from_addr,
                to_addrs=reply_msg.to_addrs,
                cc_addrs=reply_msg.cc_addrs,
                subject=reply_msg.subject,
                body_text=override_instructions,
                raw_bytes=reply_msg.raw_bytes,
            )

            next_version = state.version + 1

            review_obj = generate_review_package(
                override_input,
                system_prompt_path=SYSTEM_PROMPT_PATH,
                model=LLM_MODEL,
                previous_draft=state.draft,
                edit_version=next_version,
            )
            review_body = render_review(review_obj)

            _send_internal_review(
                reply_msg,
                review_body,
                logger=logger,
                subject_override=reply_msg.subject,
                review_version=next_version,
            )

            mark_imap_message_seen(reply_msg.uid, logger=logger)

        except Exception as exc:
            logger.exception(
                "Unhandled error processing sender reply uid=%s",
                reply_msg.uid,
            )


# -----------------------------------------------------------------------------
# Pipeline
# -----------------------------------------------------------------------------

def pipeline_run() -> None:
    logger = configure_logging("ali_pipeline")
    _phase1_new_messages(logger=logger)
    _phase2_sender_replies(logger=logger)
    logger.info("Pipeline run finished.")


# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    while True:
        pipeline_run()
        interval_minutes = get_env_int(
            "ALI_POLL_INTERVAL_MINUTES",
            _default_poll_interval_minutes(),
        )
        time.sleep(interval_minutes * 60)
