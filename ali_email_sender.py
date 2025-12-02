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
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as StdEmailMessage
from email.utils import parseaddr, formataddr
from typing import Optional

from utils_config import load_env, configure_logging, get_env_flag  # type: ignore :contentReference[oaicite:1]{index=1}
from ali_email_fetcher import EmailMessage  # type: ignore :contentReference[oaicite:2]{index=2}
import imaplib
from utils_imap import build_ssl_context, encode_imap_utf7, quote_mailbox  # type: ignore


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

    # 將原信內容附在回覆信底部，模擬一般郵件客戶端的「回覆」行為
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


def _append_to_imap_sent(msg: StdEmailMessage, logger) -> None:
    """
    將已送出的郵件寫入 IMAP「寄件備份」資料夾。

    依賴環境變數（與 fetcher 保持一致）：
    - IMAP_HOST / IMAP_PORT / IMAP_USER / IMAP_PASSWORD
    - IMAP_VERIFY_SSL（選用，預設 true）
    - IMAP_TIMEOUT（選用，預設 300）
    - IMAP_SENT_FOLDER（選用，預設 "Sent"）

    若 IMAP_* 未完整設定，則直接略過，不影響寄信流程。
    """
    host = _get_env_str("IMAP_HOST", "")
    user = _get_env_str("IMAP_USER", "")
    password = _get_env_str("IMAP_PASSWORD", "")

    if not host or not user or not password:
        if logger:
            logger.debug("IMAP_* 未完整設定，略過 APPEND 至 Sent。")
        return

    port = _get_env_int("IMAP_PORT", 993)
    verify_ssl = get_env_flag("IMAP_VERIFY_SSL", True)
    timeout = _get_env_int("IMAP_TIMEOUT", 300)
    folder = _get_env_str("IMAP_SENT_FOLDER", "Sent")

    # 處理資料夾名稱（含 UTF-7 與 quoting）
    try:
        folder.encode("ascii")
        mailbox_name = quote_mailbox(folder)
    except UnicodeEncodeError:
        mailbox_name = quote_mailbox(encode_imap_utf7(folder))

    # 嘗試一般 TLS，若遇到 DH_KEY_TOO_SMALL 則改用 legacy TLS
    using_legacy = False
    while True:
        try:
            ctx = build_ssl_context(verify_ssl, legacy=using_legacy)
            conn = imaplib.IMAP4_SSL(
                host,
                port,
                ssl_context=ctx,
                timeout=timeout,
            )
            try:
                conn.login(user, password)
                raw_bytes = msg.as_bytes()
                status, resp = conn.append(mailbox_name, None, None, raw_bytes)
                if status != "OK" and logger:
                    logger.warning("IMAP APPEND 至 %s 失敗：%s %s", folder, status, resp)
                elif logger:
                    logger.info("已將信件寫入 IMAP 資料夾 %s。", folder)
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass
            break
        except ssl.SSLError as exc:
            msg_text = str(exc)
            if "dh key too small" in msg_text.lower() and not using_legacy:
                using_legacy = True
                if logger:
                    logger.warning(
                        "IMAP 伺服器要求弱 DH 參數；改用 legacy TLS 再試一次。"
                    )
                continue
            if logger:
                logger.warning("寫入已寄信到 IMAP Sent 失敗（SSL）：%s", exc)
            break
        except Exception as exc:
            if logger:
                logger.warning("寫入已寄信到 IMAP Sent 失敗：%s", exc)
            break


def _mark_imap_message_seen(original: EmailMessage, logger) -> None:
    """
    寄信成功後，將原始郵件由 UNSEEN 標記為 SEEN。

    依賴環境變數：
    - IMAP_HOST / IMAP_PORT / IMAP_USER / IMAP_PASSWORD
    - IMAP_VERIFY_SSL（選用，預設 true）
    - IMAP_TIMEOUT（選用，預設 300）
    - IMAP_FOLDER（選用，預設 "INBOX"：原信所在資料夾）
    """
    host = _get_env_str("IMAP_HOST", "")
    user = _get_env_str("IMAP_USER", "")
    password = _get_env_str("IMAP_PASSWORD", "")

    if not host or not user or not password:
        if logger:
            logger.debug("IMAP_* 未完整設定，略過標記已讀。")
        return

    port = _get_env_int("IMAP_PORT", 993)
    verify_ssl = get_env_flag("IMAP_VERIFY_SSL", True)
    timeout = _get_env_int("IMAP_TIMEOUT", 300)
    folder = _get_env_str("IMAP_FOLDER", "INBOX")

    # 處理資料夾名稱
    try:
        folder.encode("ascii")
        mailbox_name = quote_mailbox(folder)
    except UnicodeEncodeError:
        mailbox_name = quote_mailbox(encode_imap_utf7(folder))

    using_legacy = False
    while True:
        try:
            ctx = build_ssl_context(verify_ssl, legacy=using_legacy)
            conn = imaplib.IMAP4_SSL(
                host,
                port,
                ssl_context=ctx,
                timeout=timeout,
            )
            try:
                status, _ = conn.login(user, password)
                if status != "OK" and logger:
                    logger.warning("IMAP 登入失敗，無法標記已讀。")
                    return

                status, _ = conn.select(mailbox_name, readonly=False)
                if status != "OK":
                    if logger:
                        logger.warning("無法選取資料夾 %s，略過標記已讀。", folder)
                    return

                uid_str = str(original.uid)
                status, resp = conn.uid("STORE", uid_str, "+FLAGS", r"(\Seen)")
                if status != "OK" and logger:
                    logger.warning(
                        "IMAP 對 UID %s 標記 \\Seen 失敗：%s %s", uid_str, status, resp
                    )
                elif logger:
                    logger.info("已將 UID %s 標記為已讀。", uid_str)
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass
            break
        except ssl.SSLError as exc:
            msg_text = str(exc)
            if "dh key too small" in msg_text.lower() and not using_legacy:
                using_legacy = True
                if logger:
                    logger.warning(
                        "IMAP 伺服器要求弱 DH 參數；標記已讀時改用 legacy TLS 再試一次。"
                    )
                continue
            if logger:
                logger.warning("標記 IMAP 郵件已讀失敗（SSL）：%s", exc)
            break
        except Exception as exc:
            if logger:
                logger.warning("標記 IMAP 郵件已讀失敗：%s", exc)
            break


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
        # 寄信成功後，嘗試寫入 IMAP Sent（若 IMAP_* 已設定）
        _append_to_imap_sent(msg, logger)
        # 並將原始郵件從未讀改為已讀
        _mark_imap_message_seen(original, logger)
        return SendResult(ok=True)
    except Exception as exc:
        logger.error("Failed to send reply for uid=%s: %s", original.uid, exc)
        return SendResult(ok=False, error_message=str(exc))
