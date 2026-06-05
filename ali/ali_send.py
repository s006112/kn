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
from email.utils import parseaddr, getaddresses
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
_ALLOWED_DOMAIN_SUFFIX = "@ampco.com.hk"


# Safety: only reply to forward sender.

def _parse_email(value: str) -> str:
    _, addr = parseaddr(value or "")
    return addr.strip().lower()


def _require_internal_address(addr: str, *, field: str) -> str:
    email = _parse_email(addr)
    if not email or not email.endswith(_ALLOWED_DOMAIN_SUFFIX):
        raise RuntimeError(
            f"SECURITY REJECTED (INTERNAL-ONLY POLICY): "
            f"{field} must be internal address, got {addr!r}."
        )
    return email


def _validate_outbound_recipients(msg: StdEmailMessage, *, reviewer_addr: str) -> None:
    reviewer = _require_internal_address(reviewer_addr, field="reviewer")
    env_values = dotenv_values(_DOTENV_PATH)
    admin = _parse_email(str(env_values.get("ADMIN_USERNAME", "")).strip())

    to_addrs = [
        _parse_email(addr)
        for _, addr in getaddresses([msg.get("To", "")])
        if _parse_email(addr)
    ]
    if to_addrs != [reviewer]:
        raise RuntimeError(
            f"SECURITY REJECTED: outbound To must be reviewer only. "
            f"Expected {reviewer}, got {to_addrs or '<empty>'}."
        )

    cc_addrs = [
        _parse_email(addr)
        for _, addr in getaddresses([msg.get("Cc", "")])
        if _parse_email(addr)
    ]
    if cc_addrs:
        raise RuntimeError(
            f"SECURITY REJECTED: outbound Cc is not allowed, got {cc_addrs}."
        )

    bcc_addrs = [
        _parse_email(addr)
        for _, addr in getaddresses([msg.get("Bcc", "")])
        if _parse_email(addr)
    ]
    expected_bcc = [admin] if admin and reviewer != admin else []
    if bcc_addrs != expected_bcc:
        raise RuntimeError(
            f"SECURITY REJECTED: outbound Bcc must be admin only. "
            f"Expected {expected_bcc or '<empty>'}, got {bcc_addrs or '<empty>'}."
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
    admin_addr = _parse_email(str(env_values.get("ADMIN_USERNAME", "")).strip())
    original_email = _parse_email(original_from_addr)
    if admin_addr and original_email != admin_addr:
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
    _validate_outbound_recipients(msg, reviewer_addr=original.from_addr)

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
    except Exception:
        logger.exception("Failed to send internal review for uid=%s", original.uid)
        return SendResult(ok=False, error_message="send_reply failed (see logs)")

    try:
        append_to_imap_sent(msg, logger)
    except Exception:
        logger.exception("Failed to append internal review to Sent for uid=%s", original.uid)

    return SendResult(ok=True)