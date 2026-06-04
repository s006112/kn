#!/usr/bin/env python3
"""
ali_send.py

职责：发送 INTERNAL review 给转发人；阻止其他收件人；尽力写入 Sent。
不负责 mark SEEN 或内容决策。完整 contract 见 ali/README.md。
调用方：ali_email.py
"""

from __future__ import annotations

import sys
from pathlib import Path
import smtplib
from email.message import EmailMessage as StdEmailMessage
from email.utils import parseaddr
from typing import Optional
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from helper.helper_config import load_env, configure_logging  # type: ignore
from helper.utils_imap_config import load_smtp_config  # type: ignore
from helper.utils_imap_types import EmailMessage, SendResult  # type: ignore
from helper.utils_imap_ops import append_to_imap_sent  # type: ignore

_DOTENV_PATH = ROOT / ".env"


# Safety: only reply to forward sender.

def _require_reply_to_forward_sender(original_from_addr: str, to_header_value: str) -> None:
    """仅允许回复转发给 ALI 的 reviewer；按 addr-spec 严格比对。"""
    if not original_from_addr or not to_header_value:
        raise RuntimeError(
            "SECURITY REJECTED (FORWARD-ONLY POLICY): "
            "Missing sender or recipient address."
        )

    _, original_email = parseaddr(original_from_addr)
    _, to_email = parseaddr(to_header_value)

    if not original_email or not to_email:
        raise RuntimeError(
            "SECURITY REJECTED (FORWARD-ONLY POLICY): "
            f"Unable to parse email address "
            f"(from={original_from_addr}, to={to_header_value})."
        )

    if original_email.strip().lower() != to_email.strip().lower():
        raise RuntimeError(
            "SECURITY REJECTED (FORWARD-ONLY POLICY): "
            f"Outbound recipient mismatch. "
            f"Expected To={original_email}, but got To={to_email}. "
            "Only the forwarding reviewer is allowed as recipient."
        )


# Build outgoing message.

def _build_subject(original_subject: str) -> str:
    if not original_subject:
        return "Re:"
    lower = original_subject.lstrip().lower()
    if lower.startswith("re:"):
        return original_subject
    return f"Re: {original_subject}"


def _build_to_address(from_addr: str) -> str:
    """从 reviewer addr-spec 解析回复对象。"""
    name, addr = parseaddr(from_addr)
    return addr or from_addr or ""


def _add_admin_bcc(msg: StdEmailMessage, original_from_addr: str) -> None:
    env_values = dotenv_values(_DOTENV_PATH)
    admin_addr = str(env_values.get("ADMIN_USERNAME", "")).strip().lower()
    _, original_email = parseaddr(original_from_addr)
    if admin_addr and original_email.strip().lower() != admin_addr:
        msg["Bcc"] = admin_addr


def _build_message(
    original: EmailMessage,
    reply_body: str,
    from_addr: str,
) -> StdEmailMessage:
    msg = StdEmailMessage()
    msg["From"] = from_addr
    msg["To"] = _build_to_address(original.from_addr)
    msg["Subject"] = _build_subject(original.subject)
    if original.message_id:
        msg["In-Reply-To"] = original.message_id
        msg["References"] = original.message_id

    body = (reply_body or "").rstrip()
    original_body = (original.body_text or "").strip()
    if original_body:
        body = (
            f"{body}\n\n" if body else ""
        ) + "\n".join(
            [
                "-----Original Message-----",
                *[
                    f"{label}: {value}"
                    for label, value in (
                        ("From", original.from_addr),
                        ("To", ", ".join(original.to_addrs) if original.to_addrs else ""),
                        ("Subject", original.subject),
                    )
                    if value
                ],
                "",
                "\n".join(
                    f"> {line}" if line.strip() else ">"
                    for line in original_body.splitlines()
                ),
            ]
        )

    msg.set_content(body, subtype="plain", charset="utf-8")
    return msg


# Public API.

def send_reply(
    original: EmailMessage,
    reply_body: str,
    *,
    from_addr: Optional[str] = None,
) -> SendResult:
    """发送 INTERNAL review 给转发 reviewer；不 mark SEEN。"""
    load_env()
    logger = configure_logging("ali_email_sender")

    smtp_cfg = load_smtp_config()
    if smtp_cfg is None:
        return SendResult(ok=False, error_message="Missing SMTP_HOST/USER/PASSWORD")

    msg = _build_message(original, reply_body, from_addr or smtp_cfg.default_from)
    msg["Reply-To"] = smtp_cfg.user
    _add_admin_bcc(msg, original.from_addr)
    _require_reply_to_forward_sender(original.from_addr, msg["To"])

    try:
        if smtp_cfg.use_ssl:
            smtp_server = smtplib.SMTP_SSL(smtp_cfg.host, smtp_cfg.port, timeout=60)
        else:
            smtp_server = smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=60)

        with smtp_server as server:
            server.ehlo()
            if smtp_cfg.use_starttls and not smtp_cfg.use_ssl:
                server.starttls()
                server.ehlo()
            server.login(smtp_cfg.user, smtp_cfg.password)
            server.send_message(msg)
        logger.info("Sent INTERNAL review to %s (uid=%s)", msg["To"], original.uid)
        append_to_imap_sent(msg, logger)
        return SendResult(ok=True)
    except Exception:
        logger.exception("Failed to send internal review for uid=%s", original.uid)
        return SendResult(ok=False, error_message="send_reply failed (see logs)")
