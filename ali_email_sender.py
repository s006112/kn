#!/usr/bin/env python3
"""
ali_email_sender.py

職責：
- 把 LLM 產生的 reply body 包裝成一封 email
- 設定 To / From / Subject / In-Reply-To / References
- 經由 SMTP 寄出
"""

from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage as StdEmailMessage
from email.utils import parseaddr, formataddr
from typing import Optional

from utils_config import load_env, configure_logging, get_env_flag  # type: ignore :contentReference[oaicite:1]{index=1}
from ali_email_fetcher import EmailMessage  # type: ignore :contentReference[oaicite:2]{index=2}


@dataclass
class SendResult:
    ok: bool
    error_message: Optional[str] = None


def _get_env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value else default


def _get_env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _build_subject(original_subject: str) -> str:
    if not original_subject:
        return "Re:"
    lower = original_subject.lstrip().lower()
    if lower.startswith("re:"):
        return original_subject
    return f"Re: {original_subject}"


def _build_to_address(from_header: str) -> str:
    name, addr = parseaddr(from_header)
    return addr or from_header or ""


def _build_message(
    original: EmailMessage,
    reply_body: str,
    from_addr: str,
) -> StdEmailMessage:
    msg = StdEmailMessage()
    msg["From"] = from_addr
    to_addr = _build_to_address(original.from_addr)
    msg["To"] = to_addr

    subject = _build_subject(original.subject)
    msg["Subject"] = subject

    if original.message_id:
        msg["In-Reply-To"] = original.message_id
        msg["References"] = original.message_id

    msg.set_content(reply_body or "", subtype="plain", charset="utf-8")
    return msg


def send_reply(
    original: EmailMessage,
    reply_body: str,
    *,
    from_addr: Optional[str] = None,
) -> SendResult:
    """
    對外主入口：
    - original: ali_email_fetcher.EmailMessage
    - reply_body: LLM 產生的 email 正文（純文字）
    - from_addr: 若為 None 則使用 ALI_ASSISTANT_EMAIL 或 SMTP_USER

    依賴環境變數：
    - SMTP_HOST
    - SMTP_PORT（預設 587）
    - SMTP_USER
    - SMTP_PASSWORD
    - SMTP_USE_SSL（預設 false）
    - SMTP_STARTTLS（預設 true；當 USE_SSL=false 時生效）
    - ALI_ASSISTANT_EMAIL（預設使用 SMTP_USER）
    """
    load_env()
    logger = configure_logging("ali_email_sender")

    host = _get_env_str("SMTP_HOST", "")
    port = _get_env_int("SMTP_PORT", 587)
    user = _get_env_str("SMTP_USER", "")
    password = _get_env_str("SMTP_PASSWORD", "")
    if not host or not user or not password:
        return SendResult(ok=False, error_message="Missing SMTP_HOST/USER/PASSWORD")

    default_from = os.getenv("ALI_ASSISTANT_EMAIL", user)
    sender = from_addr or default_from

    use_ssl = get_env_flag("SMTP_USE_SSL", False)
    use_starttls = get_env_flag("SMTP_STARTTLS", True)

    msg = _build_message(original, reply_body, sender)

    try:
        if use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=60)
        else:
            server = smtplib.SMTP(host, port, timeout=60)

        with server:
            server.ehlo()
            if not use_ssl and use_starttls:
                server.starttls()
                server.ehlo()
            server.login(user, password)
            server.send_message(msg)

        logger.info("Sent reply to %s (uid=%s)", msg["To"], original.uid)
        return SendResult(ok=True)
    except Exception as exc:
        logger.error("Failed to send reply for uid=%s: %s", original.uid, exc)
        return SendResult(ok=False, error_message=str(exc))
