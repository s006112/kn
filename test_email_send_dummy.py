#!/usr/bin/env python3
"""Simple helper script to trigger ali_send using values from .env."""

from __future__ import annotations

import os
import uuid

from ali_send import send_reply
from utils_config import load_env
from utils_imap_types import EmailMessage


def build_dummy_message(to_addr: str, subject: str) -> EmailMessage:
    """Create a fake incoming message so we can call send_reply directly."""
    return EmailMessage(
        uid=999999,
        message_id=f"<tester-{uuid.uuid4()}@example.com>",
        from_addr=to_addr,
        to_addrs=[os.getenv("ALI_ASSISTANT_EMAIL", "")],
        cc_addrs=[],
        subject=subject,
        body_text="This is a dummy incoming email body used for testing ali_send.",
        raw_bytes=b"",
    )


def main() -> None:
    load_env()

    to_addr = (
        os.getenv("ALI_TEST_TO_ADDRESS")
        or os.getenv("TEST_EMAIL_TO")
        or os.getenv("SMTP_USER")
        or os.getenv("ALI_ASSISTANT_EMAIL")
    )
    if not to_addr:
        raise SystemExit(
            "Missing ALI_TEST_TO_ADDRESS / TEST_EMAIL_TO / SMTP_USER / ALI_ASSISTANT_EMAIL."
        )

    from_override = os.getenv("ALI_TEST_FROM_OVERRIDE")
    subject = os.getenv("ALI_TEST_SUBJECT", "Tester dummy thread")
    reply_body = os.getenv(
        "ALI_TEST_REPLY_BODY",
        "This is a dummy reply sent via tester.py to verify ali_send().",
    )

    original = build_dummy_message(to_addr, subject)
    result = send_reply(original, reply_body, from_addr=from_override)

    if result.ok:
        print(f"[OK] Dummy reply sent to {to_addr}")
    else:
        print(f"[FAIL] Could not send dummy reply: {result.error_message}")


if __name__ == "__main__":
    main()
