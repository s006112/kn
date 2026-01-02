#!/usr/bin/env python3
"""
ali_email.py

SYSTEM INVARIANTS (NON-NEGOTIABLE)

1. No Autonomous Action
   The system MUST NOT send any message to customers or third parties autonomously.
   All generated content is internal-only unless a human explicitly copies and sends it.

2. Silence Means Termination
   If the email-sender does not reply with any non-empty content,
   the system MUST treat the review as rejected and MUST NOT continue processing.

3. Any Reply Is an Override
   Any non-empty reply from the email-sender MUST be interpreted as override instructions
   and MUST trigger a regenerated internal review using that reply as hard constraints.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from helper.utils_config import configure_logging, get_env_int  # type: ignore
from helper.utils_imap_types import EmailMessage, SendResult

from ali_fetch import fetch_new_messages, fetch_sender_replies  # type: ignore
from ali_llm import generate_review_package, render_review  # type: ignore
from ali_send import send_reply  # type: ignore


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

LLM_MODEL = "gpt-4.1-mini"
SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompt" / "prompt_ali_system.txt"

_HKT_ZONE = ZoneInfo("Asia/Hong_Kong")
_DAY_START = dt_time(9, 0)
_DAY_END = dt_time(18, 0)

_REVIEW_SUBJECT_MARKER = "[vX]"
_REVIEW_SUBJECT_PATTERN = re.compile(r"\[v\d+\]")


def _strip_review_subject_marker(subject: str) -> str:
    cleaned = re.sub(r"\[v\d+\]", "", subject or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:\s*re:\s*)+", "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split())


def _build_review_subject(subject: str, version: int) -> str:
    marker = _REVIEW_SUBJECT_MARKER.replace("X", str(version))
    base_subject = _strip_review_subject_marker(subject)
    return f"{marker} {base_subject}".strip() if base_subject else marker


def _is_review_subject(subject: str) -> bool:
    return bool(_REVIEW_SUBJECT_PATTERN.search(subject or ""))


def _default_poll_interval_minutes(now: datetime | None = None) -> int:
    current = now or datetime.now(tz=_HKT_ZONE)
    local_time = current.timetz().replace(tzinfo=None)
    return 1 if _DAY_START <= local_time < _DAY_END else 1


# -----------------------------------------------------------------------------
# Internal helpers (NO STATE, PURE PARSING)
# -----------------------------------------------------------------------------

def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _dequote_email_history(body_text: str) -> str:
    """
    Many email clients quote history with leading ">" (or ">>", etc.).
    Dequote for parsing so we can reliably find our own markers.
    """
    dequoted_lines: list[str] = []
    for line in body_text.splitlines():
        while True:
            match = re.match(r"^\s*>+\s?(.*)$", line)
            if not match:
                break
            line = match.group(1)
        dequoted_lines.append(line)
    return "\n".join(dequoted_lines)


def _search_space_from_last_anchor(body_text: str, anchor: str) -> str:
    start_search_at = body_text.rfind(anchor)
    return body_text[start_search_at:] if start_search_at != -1 else body_text


def _send_internal_review(
    original: EmailMessage,
    review_body: str,
    *,
    logger,
    subject_override: str | None = None,
    review_version: int = 1,
) -> None:
    """
    Send INTERNAL review back to the email sender only.
    """
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


def extract_last_review_draft(review_email: EmailMessage) -> str:
    """
    Extract the previous draft from an [ALI REVIEW] email.
    """
    body = _dequote_email_history(_normalize_newlines(review_email.body_text or ""))
    anchor = "[ALI INTERNAL REVIEW — NOT FOR CUSTOMER]"
    search_space = _search_space_from_last_anchor(body, anchor)

    # v1+ drafts start with: "EDIT VERSION: vX"
    header_match = None
    for match in re.finditer(r"^EDIT VERSION:\s*v(\d+)\s*$", search_space, flags=re.MULTILINE):
        header_match = match

    if header_match is None:
        raise ValueError("Cannot locate EDIT VERSION header in review email")

    after_header = search_space[header_match.end() :]

    after_header = after_header.lstrip("\n")

    # Draft ends at the internal "action required" block (or the first separator line).
    sep_pattern = re.compile(r"^[=]{10,}\s*$")
    kept: list[str] = []
    for line in after_header.splitlines():
        if line.strip() == "SENDER ACTION REQUIRED":
            break
        if sep_pattern.match(line.strip()):
            break
        kept.append(line)

    return "\n".join(kept).strip()


def extract_last_version(review_email: EmailMessage) -> int:
    body = _dequote_email_history(_normalize_newlines(review_email.body_text or ""))
    anchor = "[ALI INTERNAL REVIEW — NOT FOR CUSTOMER]"
    search_space = _search_space_from_last_anchor(body, anchor)

    versions = [int(m.group(1)) for m in re.finditer(r"EDIT VERSION:\s*v(\d+)", search_space)]
    return max(versions) if versions else 1

def _extract_override_instructions(body_text: str) -> str:
    """
    Extract only the sender's top reply text (override instructions),
    excluding quoted history.
    """
    if not body_text:
        return ""
    marker = "-----Original Message-----"
    if marker in body_text:
        body_text = body_text.split(marker, 1)[0]
    return body_text.strip()


# -----------------------------------------------------------------------------
# Pipeline
# -----------------------------------------------------------------------------

def pipeline_run() -> None:
    logger = configure_logging("ali_pipeline")

    # ---------------------------------------------------------
    # Phase 1: New incoming emails → v1 INTERNAL review
    # ---------------------------------------------------------
    messages = fetch_new_messages(max_messages=2)
    if not messages:
        logger.info("No new messages to process.")
    else:
        for msg in messages:
            try:
                logger.info("Processing new email uid=%s subject=%s", msg.uid, msg.subject)

                # Avoid treating [ALI REVIEW] threads as brand-new inbound messages.
                # These should be handled in Phase 2 (sender overrides).
                if _is_review_subject(msg.subject or ""):
                    logger.info(
                        "Skipping review-thread message in Phase 1 uid=%s subject=%s",
                        msg.uid,
                        msg.subject,
                    )
                    continue

                review_obj = generate_review_package(
                    msg,
                    system_prompt_path=SYSTEM_PROMPT_PATH,
                    model=LLM_MODEL,
                )
                review_body = render_review(review_obj)

                _send_internal_review(msg, review_body, logger=logger)

            except Exception as exc:
                logger.error("Unhandled error processing uid=%s: %s", msg.uid, exc)

    # ---------------------------------------------------------
    # Phase 2: Sender replies → EDIT v2 / v3 / ...
    # ---------------------------------------------------------
    sender_replies = fetch_sender_replies()
    if not sender_replies:
        logger.info("No sender replies to process.")
        logger.info("Pipeline run finished.")
        return

    for reply_msg in sender_replies:
        try:
            logger.info(
                "Processing sender reply uid=%s subject=%s",
                reply_msg.uid,
                reply_msg.subject,
            )

            # Silence check (defensive)
            override_instructions = _extract_override_instructions(reply_msg.body_text or "")
            if not override_instructions:
                logger.info("Empty reply body detected; treated as REJECT.")
                continue

            # Parse previous context from the replied ALI REVIEW
            last_review_draft = extract_last_review_draft(reply_msg)
            last_version = extract_last_version(reply_msg)

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
                previous_draft=last_review_draft,
                edit_version=last_version + 1,
            )
            review_body = render_review(review_obj)

            _send_internal_review(
                reply_msg,
                review_body,
                logger=logger,
                subject_override=reply_msg.subject,
                review_version=last_version + 1,
            )

        except Exception as exc:
            logger.error(
                "Unhandled error processing sender reply uid=%s: %s",
                reply_msg.uid,
                exc,
            )

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
