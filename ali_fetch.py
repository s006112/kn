#!/usr/bin/env python3
"""
Minimal email fetcher for AI assistant:

- 使用 utils_config.load_env() + configure_logging()
- 使用 utils_imap.ImapClient 抓取 UNSEEN 郵件
- 將 MIME 解析成簡單的 EmailMessage 結構
- 可選地透過 state_store 避免重複處理（若提供）

依賴環境變數：
- IMAP_HOST
- IMAP_PORT（可選，預設 993）
- IMAP_USER
- IMAP_PASSWORD
- IMAP_FOLDER（可選，預設 "INBOX"）
- IMAP_VERIFY_SSL（可選，"true"/"false"，預設 true）
- IMAP_TIMEOUT（可選，秒數，預設 300）
"""

from __future__ import annotations

from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message
from typing import Iterable, List, Optional, Protocol

from utils_config import load_env, configure_logging, get_env_flag, get_env_int  # type: ignore
from utils_imap import ImapClient, RawFetchedRecord  # type: ignore


# ------------------------------------------------------------
# Domain model
# ------------------------------------------------------------

@dataclass
class EmailMessage:
    uid: int
    message_id: str
    from_addr: str
    to_addrs: List[str]
    cc_addrs: List[str]
    subject: str
    body_text: str
    raw_bytes: bytes


class StateStoreLike(Protocol):
    """最小約定：只要有 has_processed(uid) 就可以拿來用。"""

    def has_processed(self, uid: int) -> bool:  # pragma: no cover - protocol only
        ...


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _get_env_str(name: str, default: str) -> str:
    import os

    value = os.getenv(name)
    if not value:
        return default
    return value


def _extract_addresses(header_value: Optional[str]) -> List[str]:
    """非常簡化版，只 split ';' / ','；後面若要更精確再改用 email.utils.getaddresses。"""
    if not header_value:
        return []
    parts = []
    for sep in (";", ","):
        if sep in header_value:
            parts = [p.strip() for p in header_value.split(sep)]
            break
    if not parts:
        parts = [header_value.strip()]
    return [p for p in parts if p]


def _extract_best_body(msg: Message) -> str:
    """
    MVS 版：優先 text/plain，忽略附件。
    若沒有 text/plain，就嘗試用 payload decode 後當作純文字。
    """
    # multipart：尋找第一個 text/plain 且非附件
    if msg.is_multipart():
        for part in msg.walk():
            content_type = (part.get_content_type() or "").lower()
            disp = (part.get("Content-Disposition") or "").lower()
            if content_type == "text/plain" and "attachment" not in disp:
                try:
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
        # 找不到 text/plain，就 fall back 到第一個可 decode 的 part
        for part in msg.walk():
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                continue
        return ""
    # non-multipart：直接 decode
    try:
        payload = msg.get_payload(decode=True)
        if payload is None:
            return ""
        return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return ""


def _record_to_email(record: RawFetchedRecord) -> EmailMessage:
    """將 RawFetchedRecord 轉成高階 EmailMessage 結構。"""
    msg = message_from_bytes(record.raw_bytes)

    # 標頭
    message_id = msg.get("Message-ID", "").strip()
    from_addr = msg.get("From", "").strip()
    to_addrs = _extract_addresses(msg.get("To"))
    cc_addrs = _extract_addresses(msg.get("Cc"))
    subject = msg.get("Subject", "").strip()

    body_text = _extract_best_body(msg)

    return EmailMessage(
        uid=record.uid,
        message_id=message_id,
        from_addr=from_addr,
        to_addrs=to_addrs,
        cc_addrs=cc_addrs,
        subject=subject,
        body_text=body_text,
        raw_bytes=record.raw_bytes,
    )


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def fetch_new_messages(
    state: Optional[StateStoreLike] = None,
    *,
    max_messages: Optional[int] = None,
) -> List[EmailMessage]:
    """
    從 IMAP 取得「待處理」郵件列表（UNSEEN + 可選 state 過濾），轉成 EmailMessage。

    - state 為 None：只用 UNSEEN 過濾
    - state 不為 None：在 UNSEEN 基礎上，再排除 state.has_processed(uid) == True 的

    回傳：EmailMessage[]
    """
    # 1) env + logger
    load_env()
    logger = configure_logging("email_fetcher")

    host = _get_env_str("IMAP_HOST", "")
    user = _get_env_str("IMAP_USER", "")
    password = _get_env_str("IMAP_PASSWORD", "")

    if not host or not user or not password:
        raise RuntimeError("IMAP_HOST / IMAP_USER / IMAP_PASSWORD 必須設定。")

    port = get_env_int("IMAP_PORT", 993)
    folder = _get_env_str("IMAP_FOLDER", "INBOX")
    verify_ssl = get_env_flag("IMAP_VERIFY_SSL", True)
    timeout = get_env_int("IMAP_TIMEOUT", 300)

    logger.debug(
        "IMAP config: host=%s port=%s folder=%s verify_ssl=%s timeout=%s",
        host,
        port,
        folder,
        verify_ssl,
        timeout,
    )

    client = ImapClient(
        server=host,
        port=port,
        user=user,
        password=password,
        verify_ssl=verify_ssl,
        timeout=timeout,
        logger=logger,
    )
    client.connect()

    try:
        # 2) 搜尋 UNSEEN
        criteria = ["UNSEEN"]
        uids = client.search_uids(folder, criteria)
        if not uids:
            logger.info("No UNSEEN messages in folder %s.", folder)
            return []

        # 3) 透過 state 過濾已處理
        if state is not None:
            uids = [uid for uid in uids if not state.has_processed(uid)]
            if not uids:
                logger.info("All UNSEEN messages are already processed by state store.")
                return []

        if max_messages is not None and len(uids) > max_messages:
            uids = uids[:max_messages]

        logger.info("Fetching %d messages from folder %s.", len(uids), folder)

        # 4) 一次性 fetch（MVS：先不做 chunk）
        records = client.fetch_batch(folder, uids)

        # 5) RawFetchedRecord -> EmailMessage
        messages = [_record_to_email(rec) for rec in records]

        logger.info("Fetched %d messages.", len(messages))
        return messages

    finally:
        client.disconnect()
