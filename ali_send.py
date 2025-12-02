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
import ssl
import imaplib
from email.message import EmailMessage as StdEmailMessage
from email.utils import parseaddr
from typing import Optional, Callable

from utils_config import load_env, configure_logging  # type: ignore
from utils_mail_config import load_imap_config, load_smtp_config  # type: ignore
from utils_mail_types import EmailMessage, SendResult  # type: ignore
from utils_imap_client import build_ssl_context, encode_imap_utf7, quote_mailbox  # type: ignore


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


# ------------------------------------------------------------
# IMAP helpers：pure core + wrapper
# ------------------------------------------------------------

def _resolve_mailbox_name(folder: str) -> str:
    """
    將人類可讀的資料夾名稱轉成 IMAP protocol 名稱（處理 UTF-7 + quoting）。
    """
    try:
        folder.encode("ascii")
        return quote_mailbox(folder)
    except UnicodeEncodeError:
        return quote_mailbox(encode_imap_utf7(folder))


def _with_imap_connection(imap_cfg, logger, action: Callable[[imaplib.IMAP4], None], context: str) -> None:
    """
    通用 IMAP 連線封裝：
    - 處理 TLS / legacy TLS 重試
    - 登入 / logout
    - 執行給定 action(conn)
    """
    using_legacy = False
    while True:
        try:
            ctx = build_ssl_context(imap_cfg.verify_ssl, legacy=using_legacy)
            conn = imaplib.IMAP4_SSL(
                imap_cfg.host,
                imap_cfg.port,
                ssl_context=ctx,
                timeout=imap_cfg.timeout,
            )
            try:
                status, _ = conn.login(imap_cfg.user, imap_cfg.password)
                if status != "OK":
                    if logger:
                        logger.warning("IMAP 登入失敗，無法執行動作：%s。", context)
                    return

                action(conn)
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
                        "IMAP 伺服器要求弱 DH 參數；%s 時改用 legacy TLS 再試一次。", context
                    )
                continue
            if logger:
                logger.warning("%s 失敗（SSL）：%s", context, exc)
            break
        except Exception as exc:
            if logger:
                logger.warning("%s 失敗：%s", context, exc)
            break


# ---------- pure core 1：append sent ----------

def append_to_sent_with_connection(
    conn: imaplib.IMAP4,
    mailbox_name: str,
    display_folder: str,
    msg: StdEmailMessage,
    logger=None,
) -> None:
    """
    純邏輯版本：
    - 不讀 env
    - 不處理 TLS / legacy
    - 不管理 connect / logout
    只負責在指定 mailbox 做 APPEND。
    """
    raw_bytes = msg.as_bytes()
    status, resp = conn.append(mailbox_name, None, None, raw_bytes)
    if status != "OK":
        if logger:
            logger.warning(
                "IMAP APPEND 至 %s 失敗：%s %s",
                display_folder,
                status,
                resp,
            )
    elif logger:
        logger.info("已將信件寫入 IMAP 資料夾 %s。", display_folder)


def _append_to_imap_sent(msg: StdEmailMessage, logger) -> None:
    """
    Wrapper：
    - 讀 IMAP_* config
    - 決定 Sent 資料夾名稱
    - 建立連線 / TLS / 重試
    - 呼叫 append_to_sent_with_connection
    """
    imap_cfg = load_imap_config("IMAP_SENT_FOLDER", "Sent")
    if imap_cfg is None:
        if logger:
            logger.debug("IMAP_* 未完整設定，略過 APPEND 至 Sent。")
        return

    mailbox_name = _resolve_mailbox_name(imap_cfg.folder)

    _with_imap_connection(
        imap_cfg,
        logger,
        lambda conn: append_to_sent_with_connection(
            conn,
            mailbox_name,
            imap_cfg.folder,
            msg,
            logger=logger,
        ),
        context="寫入已寄信到 IMAP Sent",
    )


# ---------- pure core 2：mark seen ----------

def mark_message_seen_with_connection(
    conn: imaplib.IMAP4,
    mailbox_name: str,
    display_folder: str,
    uid: int,
    logger=None,
) -> None:
    """
    純邏輯版本：
    - 不讀 env / 不處理 TLS / 不管理連線
    只負責 select mailbox + 對指定 UID 加上 \\Seen。
    """
    status, _ = conn.select(mailbox_name, readonly=False)
    if status != "OK":
        if logger:
            logger.warning("無法選取資料夾 %s，略過標記已讀。", display_folder)
        return

    uid_str = str(uid)
    status, resp = conn.uid("STORE", uid_str, "+FLAGS", r"(\Seen)")
    if status != "OK":
        if logger:
            logger.warning(
                "IMAP 對 UID %s 標記 \\Seen 失敗：%s %s", uid_str, status, resp
            )
    elif logger:
        logger.info("已將 UID %s 標記為已讀。", uid_str)


def _mark_imap_message_seen(original: EmailMessage, logger) -> None:
    """
    Wrapper：
    - 讀 IMAP_* config
    - 決定原信所在資料夾名稱
    - 建立連線 / TLS / 重試
    - 呼叫 mark_message_seen_with_connection
    """
    imap_cfg = load_imap_config("IMAP_FOLDER", "INBOX")
    if imap_cfg is None:
        if logger:
            logger.debug("IMAP_* 未完整設定，略過標記已讀。")
        return

    mailbox_name = _resolve_mailbox_name(imap_cfg.folder)

    _with_imap_connection(
        imap_cfg,
        logger,
        lambda conn: mark_message_seen_with_connection(
            conn,
            mailbox_name,
            imap_cfg.folder,
            original.uid,
            logger=logger,
        ),
        context="標記 IMAP 郵件已讀",
    )


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

    smtp_cfg = load_smtp_config()
    if smtp_cfg is None:
        return SendResult(ok=False, error_message="Missing SMTP_HOST/USER/PASSWORD")

    sender = from_addr or smtp_cfg.default_from

    msg = _build_message(original, reply_body, sender)
    # Ensure correspondent address matches SMTP_USER for replies
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
        _append_to_imap_sent(msg, logger)
        # 並將原始郵件從未讀改為已讀
        _mark_imap_message_seen(original, logger)
        return SendResult(ok=True)
    except Exception as exc:
        logger.error("Failed to send reply for uid=%s: %s", original.uid, exc)
        return SendResult(ok=False, error_message=str(exc))
