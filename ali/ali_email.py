#!/usr/bin/env python3
"""
ali_email.py — Orchestration Layer（STABLE）

职责：
- 执行 Phase 1 / Phase 2 sequencing。
- 管理 message lifecycle、failure handling 和 Step5 subject versioning。
- 不承载 routing、retrieval、prompt construction 或 delivery policy。

完整 architecture contract、Phase / Version 定义和 invariants：
- 见 ali/README.md

Used by:
- None（application entry point）
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import re
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from helper.helper_config import configure_logging, get_env_int  # type: ignore
from helper.utils_imap_types import EmailMessage, SendResult
from helper.utils_imap_ops import mark_imap_message_seen  # type: ignore
from helper.utils_imap_client import ImapClient  # type: ignore
from helper.utils_imap_config import load_imap_config  # type: ignore

from ali.ali_fetch import fetch_new_messages, fetch_sender_replies  # type: ignore
from ali.ali_llm import generate_review_package, render_review  # type: ignore
from ali.ali_send import send_reply  # type: ignore
from ali.ali_parse import (
    REVIEW_SUBJECT_MARKER,
    REVIEW_SUBJECT_PATTERN,
    extract_last_review_state,
    extract_reviewer_reply_text,
)

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

LLM_MODEL = "sonar"
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompt" / "prompt_ali_system.txt"

_HKT_ZONE = ZoneInfo("Asia/Hong_Kong")
_DAY_START = dt_time(9, 0)
_DAY_END = dt_time(18, 0)
_FAILED_FOLDER = "Ali_failed"

def _default_poll_interval_minutes(now: datetime | None = None) -> float:
    current = now or datetime.now(tz=_HKT_ZONE)
    local_time = current.timetz().replace(tzinfo=None)
    return 1 if _DAY_START <= local_time < _DAY_END else 2

def _is_deterministic_failure(exc: Exception) -> bool:
    return isinstance(exc, (ValueError, FileNotFoundError))

def _move_imap_message_to_failed(uid: int, *, logger) -> None:
    cfg = load_imap_config(
        "IMAP_FOLDER",
        "INBOX",
        require_credentials=True,
    )
    if cfg is None:
        raise RuntimeError("IMAP configuration missing.")

    client = ImapClient(
        server=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        verify_ssl=cfg.verify_ssl,
        timeout=cfg.timeout,
        logger=logger,
    )
    client.connect()
    try:
        client.move_message(cfg.folder, uid, _FAILED_FOLDER)
    finally:
        client.disconnect()

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
    - Deterministic failures are quarantined to Ali_failed
    - Transient failures remain UNSEEN for retry
    """
    try:
        fn()
    except Exception as exc:
        logger.exception(
            "%s failed (uid=%s subject=%s)",
            ctx,
            uid,
            subject,
        )

        if uid is None or not _is_deterministic_failure(exc):
            return

        try:
            _move_imap_message_to_failed(uid, logger=logger)
            logger.error(
                "%s quarantined to %s (uid=%s subject=%s reason=%s)",
                ctx,
                _FAILED_FOLDER,
                uid,
                subject,
                type(exc).__name__,
            )
        except Exception:
            logger.exception(
                "%s failed to move message to %s (uid=%s subject=%s)",
                ctx,
                _FAILED_FOLDER,
                uid,
                subject,
            )

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

# Step5: Packaging — review-thread subject/version sequencing.
def _build_review_subject(subject: str, version: int) -> str:
    marker = REVIEW_SUBJECT_MARKER.replace("X", str(version))
    cleaned = REVIEW_SUBJECT_PATTERN.sub("", subject or "")
    cleaned = re.sub(r"^(?:\s*re:\s*)+", "", cleaned, flags=re.IGNORECASE)
    cleaned = " ".join(cleaned.split())
    return f"{cleaned} {marker}".strip() if cleaned else marker


def _send_internal_review(
    original: EmailMessage,
    review_body: str,
    *,
    logger,
    base_subject: str | None = None,
    review_version: int = 1,
) -> None:
    reviewer = original.from_addr
    if not reviewer:
        raise RuntimeError("Missing reviewer (msg.from_addr is empty)")

    subject_source = base_subject if base_subject is not None else (original.subject or "")
    subject = _build_review_subject(subject_source, review_version)

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

            if REVIEW_SUBJECT_PATTERN.search(msg.subject or ""):
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

            raw_body = (reply_msg.body_text or "").strip()
            reviewer_reply_text = extract_reviewer_reply_text(raw_body).strip()
            if not reviewer_reply_text:
                logger.info("Empty reviewer reply detected; treated as REJECT. Marking as SEEN.")
                mark_imap_message_seen(reply_msg.uid, logger=logger)
                return

            state = extract_last_review_state(reply_msg)
            next_version = state.version + 1

            reviewer_reply_input = EmailMessage(
                uid=reply_msg.uid,
                message_id=reply_msg.message_id,
                from_addr=reply_msg.from_addr,
                to_addrs=reply_msg.to_addrs,
                cc_addrs=reply_msg.cc_addrs,
                subject=reply_msg.subject,
                body_text=reviewer_reply_text,
                raw_bytes=reply_msg.raw_bytes,
            )

            review_obj = generate_review_package(
                reviewer_reply_input,
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
                base_subject=reply_msg.subject,
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
