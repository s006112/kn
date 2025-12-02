#!/usr/bin/env python3
"""
ali_email_sender.py

職責：
- 把 LLM 產生的 reply body 包裝成一封 email
- 設定 To / From / Subject / In-Reply-To / References
- 經由 SMTP 寄出，並：
  - 將已寄信寫入 IMAP 寄件備份
  - 將原信標記為已讀
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage as StdEmailMessage
from email.utils import parseaddr
from typing import Optional

from utils_config import load_env, configure_logging  # type: ignore
from utils_imap_config import load_smtp_config  # type: ignore
from utils_imap_types import EmailMessage, SendResult  # type: ignore
from utils_imap_ops import append_to_imap_sent, mark_imap_message_seen  # type: ignore


# ------------------------------------------------------------
# Build outgoing message
# ------------------------------------------------------------

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

    base_body = (reply_body or "").rstrip()
    original_body = (original.body_text or "").strip()

    if original_body:
        header_lines = []
        if original.from_addr:
            header_lines.append(f"From: {original.from_addr}")
        if original.to_addrs:
            header_lines.append(f"To: {', '.join(original.to_addrs)}")
        if original.subject:
            header_lines.append(f"Subject: {original.subject}")

        header_block = ""
        if header_lines:
            header_block = "-----Original Message-----\n" + "\n".join(header_lines)

        quoted_lines = []
        for line in original_body.splitlines():
            if line.strip():
                quoted_lines.append(f"> {line}")
            else:
                quoted_lines.append(">")
        quoted_block = "\n".join(quoted_lines)

        history_block = (
            f"{header_block}\n\n{quoted_block}" if header_block else quoted_block
        )

        if base_body:
            full_body = f"{base_body}\n\n{history_block}"
        else:
            full_body = history_block
    else:
        full_body = base_body

    msg.set_content(full_body, subtype="plain", charset="utf-8")
    return msg


# ------------------------------------------------------------
# Public API：send_reply
# ------------------------------------------------------------

def send_reply(
    original: EmailMessage,
    reply_body: str,
    *,
    from_addr: Optional[str] = None,
) -> SendResult:
    """
    對外主入口：
    - original: utils_mail_types.EmailMessage
    - reply_body: LLM 產生的 email 正文（純文字）
    - from_addr: 若為 None 則使用 ALI_ASSISTANT_EMAIL 或 SMTP_USER
    """
    load_env()
    logger = configure_logging("ali_email_sender")

    smtp_cfg = load_smtp_config()
    if smtp_cfg is None:
        return SendResult(ok=False, error_message="Missing SMTP_HOST/USER/PASSWORD")

    sender = from_addr or smtp_cfg.default_from

    msg = _build_message(original, reply_body, sender)
    msg["Reply-To"] = smtp_cfg.user

    try:
        if smtp_cfg.use_ssl:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                smtp_cfg.host, smtp_cfg.port, timeout=60
            )
        else:
            server = smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=60)

        with server:
            server.ehlo()
            if not smtp_cfg.use_ssl and smtp_cfg.use_starttls:
                server.starttls()
                server.ehlo()
            server.login(smtp_cfg.user, smtp_cfg.password)
            server.send_message(msg)

        logger.info("Sent reply to %s (uid=%s)", msg["To"], original.uid)

        # 寄信成功後，嘗試寫入 IMAP Sent（若 IMAP_* 已設定）
        append_to_imap_sent(msg, logger)

        # 並將原始郵件從未讀改為已讀
        mark_imap_message_seen(original.uid, logger)

        return SendResult(ok=True)
    except Exception as exc:
        logger.error("Failed to send reply for uid=%s: %s", original.uid, exc)
        return SendResult(ok=False, error_message=str(exc))
