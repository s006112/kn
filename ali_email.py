#!/usr/bin/env python3
"""
ali_email.py

SYSTEM INVARIANTS (NON-NEGOTIABLE)

1. No Autonomous Action
   The system MUST NOT send any message to customers or third parties autonomously.
   All generated content is INTERNAL-ONLY and sent back ONLY to the human reviewer.
   Any outbound content requires explicit human forwarding outside this system.

2. Silence Means Termination
   If the reviewer replies with NO non-empty, non-quoted content,
   the review is treated as REJECTED and processing MUST stop.

3. Any Reply Is an Override
   Any NON-EMPTY reply content written by the reviewer
   (after removing quoted history) is treated as OVERRIDE instructions
   and MUST trigger a regenerated INTERNAL review.

4. FORWARD-ONLY INPUT MODEL (CRITICAL)
   The system ONLY accepts emails that are explicitly FORWARDED by a human reviewer.

   Enforcement:
   - The email's From address MUST belong to the reviewer.
   - The system replies ONLY to the reviewer (never to original customers).
   - Emails sent on behalf of customers, rewritten From headers,
     delegated senders, or CRM-originated messages are intentionally REJECTED.

   Rationale:
   This invariant guarantees:
   - ALI never communicates directly with customers
   - All external communication remains explicitly human-mediated


CALL FLOW (ACTUAL EXECUTION PATH)

pipeline_run()
  -> _phase1_new_messages()
       Purpose:
         Generate initial INTERNAL review drafts (v1) for new inbound emails.

       Input:
         - UNSEEN emails
         - NON-review threads (subject does NOT match REVIEW_SUBJECT_PATTERN)

       Steps:
         -> ali_fetch.fetch_new_messages()
         -> ali_llm.generate_review_package()        # v1 rewrite
         -> ali_llm.render_review()
         -> _send_internal_review()                  # reviewer-only
         -> ali_send.send_reply()
         -> mark_imap_message_seen()

  -> _phase2_sender_replies()
       Purpose:
         Process reviewer replies as explicit override instructions.

       Input:
         - UNSEEN replies to prior review threads ([ALI:vN])

       Steps:
         -> ali_fetch.fetch_sender_replies()
         -> ali_mail_parse.extract_sender_override()
              (removes quoted history; extracts reviewer-written content only)
         -> ali_mail_parse.extract_last_review_state()
              (canonical LAST review version + draft)
         -> ali_llm.generate_review_package(
                previous_draft = state.draft,
                edit_version   = state.version + 1
            )                                      # edit-only, no rewrite fallback
         -> ali_llm.render_review()
         -> _send_internal_review(review_version = state.version + 1)
         -> ali_send.send_reply()
         -> mark_imap_message_seen()


ERROR HANDLING MODEL

- Each email (Phase 1) or reply (Phase 2) is processed independently.
- Exceptions are logged with full traceback.
- A failure on one message MUST NOT stop processing of subsequent messages.
- Failed messages remain UNSEEN for future inspection or retry.


DESIGN INTENT

This module is the orchestration layer.
It enforces system invariants, sequencing, and safety boundaries.

It does NOT:
- Perform IMAP fetching logic
- Parse email bodies or quoted history
- Implement LLM prompting or RAG logic
- Send messages to external recipients

Those responsibilities are delegated to:
- ali_fetch
- ali_mail_parse
- ali_llm
- ali_send
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

LLM_MODEL = "sonar"
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompt" / "prompt_ali_system.txt"

_HKT_ZONE = ZoneInfo("Asia/Hong_Kong")
_DAY_START = dt_time(9, 0)
_DAY_END = dt_time(18, 0)


def _default_poll_interval_minutes(now: datetime | None = None) -> float:
    current = now or datetime.now(tz=_HKT_ZONE)
    local_time = current.timetz().replace(tzinfo=None)
    return 0.5 if _DAY_START <= local_time < _DAY_END else 1


# -----------------------------------------------------------------------------
# Guarded execution (核心收敛点)
# -----------------------------------------------------------------------------

def _run_guarded(
    *,
    logger,
    ctx: str,
    uid: int | None = None,
    subject: str | None = None,
    fn,
) -> None:
    """
    Execute fn() with standardized exception handling.
    - Includes full traceback
    - Swallows exception to preserve pipeline semantics
    """
    try:
        fn()
    except Exception:
        logger.exception(
            "%s failed (uid=%s subject=%s)",
            ctx,
            uid,
            subject,
        )


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _build_review_subject(subject: str, version: int) -> str:
    marker = REVIEW_SUBJECT_MARKER.replace("X", str(version))
    cleaned = REVIEW_SUBJECT_PATTERN.sub("", subject or "")
    cleaned = re.sub(r"^(?:\s*re:\s*)+", "", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split())
    return f"{cleaned} {marker}".strip() if cleaned else marker


def _is_review_subject(subject: str) -> bool:
    return bool(REVIEW_SUBJECT_PATTERN.search(subject or ""))


def _send_internal_review(
    original: EmailMessage,
    review_body: str,
    *,
    logger,
    subject_override: str | None = None,
    review_version: int = 1,
) -> None:
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
        raise RuntimeError(result.error_message or "Send failed")


# -----------------------------------------------------------------------------
# Phase 1
# -----------------------------------------------------------------------------

def _phase1_new_messages(*, logger) -> None:
    messages = fetch_new_messages(max_messages=2)
    if not messages:
        logger.info("No new messages to process.")
        return

    for msg in messages:
        def _work() -> None:
            logger.info("Processing new email uid=%s subject=%s", msg.uid, msg.subject)

            if _is_review_subject(msg.subject or ""):
                logger.info("Skipping review-thread message uid=%s", msg.uid)
                return

            review_obj = generate_review_package(
                msg,
                system_prompt_path=SYSTEM_PROMPT_PATH,
                model=LLM_MODEL,
            )
            review_body = render_review(review_obj)

            _send_internal_review(msg, review_body, logger=logger)
            mark_imap_message_seen(msg.uid, logger=logger)

        _run_guarded(
            logger=logger,
            ctx="Phase1 process message",
            uid=msg.uid,
            subject=msg.subject,
            fn=_work,
        )


# -----------------------------------------------------------------------------
# Phase 2
# -----------------------------------------------------------------------------

def _phase2_sender_replies(*, logger) -> None:
    sender_replies = fetch_sender_replies()
    if not sender_replies:
        logger.info("No sender replies to process.")
        return

    for reply_msg in sender_replies:
        def _work() -> None:
            logger.info(
                "Processing sender reply uid=%s subject=%s",
                reply_msg.uid,
                reply_msg.subject,
            )

            override_instructions = extract_sender_override(reply_msg.body_text or "")
            if not override_instructions:
                logger.info("Empty reply detected; treated as REJECT. Marking as SEEN.")
                mark_imap_message_seen(reply_msg.uid, logger=logger)
                return

            state = extract_last_review_state(reply_msg)
            next_version = state.version + 1

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

        _run_guarded(
            logger=logger,
            ctx="Phase2 process sender reply",
            uid=reply_msg.uid,
            subject=reply_msg.subject,
            fn=_work,
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
