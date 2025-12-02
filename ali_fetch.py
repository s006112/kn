from __future__ import annotations

from dataclasses import dataclass
from email import message_from_bytes
from email.message import Message
from email.utils import getaddresses
from typing import Iterable, List, Optional, Protocol

from utils_config import load_env, configure_logging  # type: ignore
from utils_mail_config import load_imap_config  # type: ignore
from utils_imap_client import ImapClient, RawFetchedRecord  # type: ignore

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

def _extract_addresses(header_value: Optional[str]) -> List[str]:
    """
    RFC 準確解析版：
    - 使用 email.utils.getaddresses 處理複雜地址格式
    - 自動支援名稱、引號、多種分隔符、folded headers
    - 回傳純 email 字串清單（與你原本介面一致）
    """
    if not header_value:
        return []
    
    # 解析結果為 List[Tuple[name, address]]
    parsed = getaddresses([header_value])

    # 只取 address，並排除空字串
    return [addr for _, addr in parsed if addr]


def _extract_best_body(msg: Message) -> str:
    """
    優先回傳第一個 text/plain 且非附件的內容；
    若沒有，就回傳第一個能成功 decode 的 part。
    """
    def _decode_part(part: Message) -> Optional[str]:
        payload = part.get_payload(decode=True)
        if not payload:
            return None
        try:
            charset = part.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except Exception:
            return None

    preferred: list[Message] = []
    others: list[Message] = []

    # 不分 multipart / non-multipart，統一用 walk 掃描
    for part in msg.walk():
        content_type = (part.get_content_type() or "").lower()
        disp = (part.get("Content-Disposition") or "").lower()
        if content_type == "text/plain" and "attachment" not in disp:
            preferred.append(part)
        else:
            others.append(part)

    # 先試 preferred，再試其他可 decode 的 part
    for part in preferred + others:
        text = _decode_part(part)
        if text:
            return text

    return ""


def _get_header(msg: Message, name: str) -> str:
    """安全取得並 strip 單一字串型標頭。"""
    return (msg.get(name) or "").strip()

def _record_to_email(record: RawFetchedRecord) -> EmailMessage:
    """將 RawFetchedRecord 轉成高階 EmailMessage 結構。"""
    msg = message_from_bytes(record.raw_bytes)

    return EmailMessage(
        uid=record.uid,
        message_id=_get_header(msg, "Message-ID"),
        from_addr=_get_header(msg, "From"),
        to_addrs=_extract_addresses(msg.get("To")),
        cc_addrs=_extract_addresses(msg.get("Cc")),
        subject=_get_header(msg, "Subject"),
        body_text=_extract_best_body(msg),
        raw_bytes=record.raw_bytes,
    )


# ------------------------------------------------------------
# Public API
# ------------------------------------------------------------

def _init_imap_client():
    """建立已連線的 IMAP client，順便處理 env / config / logger。"""
    load_env()
    logger = configure_logging("email_fetcher")
    imap_cfg = load_imap_config("IMAP_FOLDER", "INBOX", require_credentials=True)

    logger.debug("IMAP config: %s", imap_cfg)

    client = ImapClient(
        server=imap_cfg.host,
        port=imap_cfg.port,
        user=imap_cfg.user,
        password=imap_cfg.password,
        verify_ssl=imap_cfg.verify_ssl,
        timeout=imap_cfg.timeout,
        logger=logger,
    )
    client.connect()
    return client, imap_cfg, logger


def fetch_new_messages(
    state: Optional[StateStoreLike] = None,
    *,
    max_messages: Optional[int] = None,
) -> List[EmailMessage]:
    """
    從 IMAP 取得「待處理」郵件列表（UNSEEN + 可選 state 過濾），轉成 EmailMessage。
    """
    client, imap_cfg, logger = _init_imap_client()

    try:
        # 1) 取得 UNSEEN UID
        uids = client.search_uids(imap_cfg.folder, ["UNSEEN"])

        # 2) state 過濾（若有）
        if state is not None:
            uids = [uid for uid in uids if not state.has_processed(uid)]

        if not uids:
            logger.info("No pending UNSEEN messages in folder %s.", imap_cfg.folder)
            return []

        # 3) 限制最多處理筆數
        if max_messages is not None:
            uids = uids[:max_messages]

        logger.info("Fetching %d messages from folder %s.", len(uids), imap_cfg.folder)

        # 4) 取得原始郵件並轉成 EmailMessage
        records = client.fetch_batch(imap_cfg.folder, uids)
        messages = [_record_to_email(rec) for rec in records]

        logger.info("Fetched %d messages.", len(messages))
        return messages

    finally:
        client.disconnect()