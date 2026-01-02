from __future__ import annotations

from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import Message
from email.policy import default
from email.utils import getaddresses
from typing import List, Optional, Protocol

from helper.utils_config import configure_logging, get_env_str, load_env  # type: ignore
from helper.utils_imap_client import ImapClient, RawFetchedRecord  # type: ignore
from helper.utils_imap_config import load_imap_config  # type: ignore
from helper.utils_imap_ops import move_imap_message_with_client  # type: ignore
from helper.utils_imap_types import EmailMessage
from helper.utils_text_processing import extract_email_body  # type: ignore


_ALLOWED_DOMAIN_SUFFIX = "@ampco.com.hk"
_REVIEW_SUBJECT_MARKER = "[ALI REVIEW]"


# ------------------------------------------------------------
# Domain model
# ------------------------------------------------------------

class StateStoreLike(Protocol):
    def has_processed(self, uid: int) -> bool:
        ...


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _extract_addresses(header_value: Optional[str]) -> List[str]:
    if not header_value:
        return []
    parsed = getaddresses([header_value])
    return [addr for _, addr in parsed if addr]


def _get_header(msg: Message, name: str) -> str:
    raw_value = (msg.get(name) or "").strip()
    if not raw_value:
        return ""
    try:
        return str(make_header(decode_header(raw_value)))
    except Exception:
        return raw_value


def _record_to_email(record: RawFetchedRecord) -> EmailMessage:
    msg = message_from_bytes(record.raw_bytes, policy=default)
    return EmailMessage(
        uid=record.uid,
        message_id=_get_header(msg, "Message-ID"),
        from_addr=_get_header(msg, "From"),
        to_addrs=_extract_addresses(msg.get("To")),
        cc_addrs=_extract_addresses(msg.get("Cc")),
        subject=_get_header(msg, "Subject"),
        body_text=extract_email_body(msg),
        raw_bytes=record.raw_bytes,
    )


def _extract_reply_body(body_text: str) -> str:
    if not body_text:
        return ""
    marker = "-----Original Message-----"
    if marker in body_text:
        body_text = body_text.split(marker, 1)[0]
    return body_text.strip()


# ------------------------------------------------------------
# Core pure function
# ------------------------------------------------------------

def fetch_new_messages_with_client(
    client: ImapClient,
    folder: str,
    *,
    state: Optional[StateStoreLike] = None,
    max_messages: Optional[int] = None,
    logger=None,
) -> List[EmailMessage]:
    """
    純邏輯版本：不建立 client、不 touch env、不做 connect/disconnect。
    """
    uids = client.search_uids(folder, ["UNSEEN"])

    if state is not None:
        uids = [uid for uid in uids if not state.has_processed(uid)]

    if not uids:
        if logger:
            logger.info("No pending UNSEEN messages in folder %s.", folder)
        return []

    if max_messages is not None:
        uids = uids[:max_messages]

    if logger:
        logger.info("Fetching %d messages from folder %s.", len(uids), folder)

    records = client.fetch_batch(folder, uids)
    messages = [_record_to_email(rec) for rec in records]

    if logger:
        logger.info("Fetched %d messages.", len(messages))

    return messages


# ------------------------------------------------------------
# Outer wrapper (actual API)
# ------------------------------------------------------------

def _init_imap_client():
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
    Public API：建立 client → 呼叫 core → disconnect。
    """
    client, imap_cfg, logger = _init_imap_client()

    try:
        messages = fetch_new_messages_with_client(
            client,
            imap_cfg.folder,
            state=state,
            max_messages=max_messages,
            logger=logger,
        )
        if not messages:
            return messages

        allowed_messages: list[EmailMessage] = []
        blocked_messages: list[EmailMessage] = []
        domain_suffix = _ALLOWED_DOMAIN_SUFFIX.lower()
        for msg in messages:
            senders = _extract_addresses(msg.from_addr)
            if any(addr.lower().endswith(domain_suffix) for addr in senders):
                allowed_messages.append(msg)
            else:
                blocked_messages.append(msg)

        if blocked_messages:
            trash_folder = get_env_str("IMAP_TRASH_FOLDER", "Trash")
            for msg in blocked_messages:
                if logger:
                    logger.info(
                        "Dropping uid=%s from disallowed sender %s; moving to %s.",
                        msg.uid,
                        msg.from_addr or "<unknown>",
                        trash_folder,
                    )
                move_imap_message_with_client(
                    client,
                    imap_cfg.folder,
                    msg.uid,
                    trash_folder,
                    logger=logger,
                )

        return allowed_messages
    finally:
        client.disconnect()


def fetch_sender_replies(
    *,
    max_messages: Optional[int] = None,
) -> List[EmailMessage]:
    """
    Fetch unread sender replies to ALI review messages.
    Returns only replies with non-empty body text.
    """
    client, imap_cfg, logger = _init_imap_client()

    try:
        criteria = ["UNSEEN", "SUBJECT", f'"{_REVIEW_SUBJECT_MARKER}"']
        uids = client.search_uids(imap_cfg.folder, criteria)

        if not uids:
            if logger:
                logger.info("No unread sender replies found.")
            return []

        if max_messages is not None:
            uids = uids[:max_messages]

        records = client.fetch_batch(imap_cfg.folder, uids)
        replies: list[EmailMessage] = []
        for record in records:
            msg = _record_to_email(record)
            reply_body = _extract_reply_body(msg.body_text)
            if not reply_body.strip():
                continue
            replies.append(
                EmailMessage(
                    uid=msg.uid,
                    message_id=msg.message_id,
                    from_addr=msg.from_addr,
                    to_addrs=msg.to_addrs,
                    cc_addrs=msg.cc_addrs,
                    subject=msg.subject,
                    body_text=reply_body,
                    raw_bytes=msg.raw_bytes,
                )
            )

        if logger:
            logger.info("Fetched %d sender replies.", len(replies))

        return replies
    finally:
        client.disconnect()
