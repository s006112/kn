from __future__ import annotations

"""
ali_fetch.py

IMAP FETCH ROUTINES (ARROW VIEW)

`fetch_new_messages()`
  -> `_init_imap_client()` (load env + config + connect)
  -> `fetch_new_messages_with_client()` (pure-ish core: search UNSEEN -> fetch -> parse)
  -> (post-filter) allow only `_ALLOWED_DOMAIN_SUFFIX` senders
       -> move disallowed messages to `IMAP_TRASH_FOLDER`
  -> disconnect

`fetch_sender_replies()`
  -> `_init_imap_client()`
  -> search UNSEEN with review-subject query
  -> fetch -> parse -> `extract_top_reply()` (must be non-empty override instructions)
  -> disconnect

Notes
- `_record_to_email()` keeps the full raw bytes and full `body_text` (including quoted history)
  so downstream code can re-parse prior drafts/versions from a replied review email.
"""

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

from ali_mail_parse import extract_top_reply
from ali_review_proto import REVIEW_SUBJECT_IMAP_QUERY, REVIEW_SUBJECT_PATTERN


_ALLOWED_DOMAIN_SUFFIX = "@ampco.com.hk"


# ------------------------------------------------------------
# Domain model
# ------------------------------------------------------------

class StateStoreLike(Protocol):
    """Minimal interface for stateful de-duplication (e.g. "already processed uid")."""
    def has_processed(self, uid: int) -> bool:
        ...


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _extract_addresses(header_value: Optional[str]) -> List[str]:
    """Parse an address header value into a list of email addrs (drops display names)."""
    if not header_value:
        return []
    parsed = getaddresses([header_value])
    return [addr for _, addr in parsed if addr]


def _get_header(msg: Message, name: str) -> str:
    """Read and best-effort decode a header from an email.message.Message."""
    raw_value = (msg.get(name) or "").strip()
    if not raw_value:
        return ""
    try:
        return str(make_header(decode_header(raw_value)))
    except Exception:
        return raw_value


def _record_to_email(record: RawFetchedRecord) -> EmailMessage:
    """Convert a low-level IMAP fetch record into the project `EmailMessage` DTO."""
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
    Core fetch routine using an already-connected `ImapClient`.

    Design goal: keep this function "pure-ish" for easier testing/reuse:
    - Does NOT load env vars
    - Does NOT connect/disconnect
    - Only performs IMAP ops via the provided client
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
    """Initialize and connect an IMAP client using env-driven config."""
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
    Public API wrapper: init client -> core fetch -> domain filter -> disconnect.
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
                # We "quarantine" disallowed senders by moving them out of the inbox,
                # so they won't be re-fetched as UNSEEN in the next poll loop.
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
    Returns unread emails whose SUBJECT contains the review marker and whose
    extracted override instructions (top reply section) are non-empty.

    Note: We intentionally keep the FULL body_text (including quoted history)
    so the caller can parse prior drafts/version from the replied review email.
    """
    client, imap_cfg, logger = _init_imap_client()

    try:
        criteria = ["UNSEEN", "SUBJECT", f'"{REVIEW_SUBJECT_IMAP_QUERY}"']
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
            if not REVIEW_SUBJECT_PATTERN.search(msg.subject or ""):
                continue
            override_instructions = extract_top_reply(msg.body_text)
            if not override_instructions.strip():
                continue
            # Keep full body_text for downstream parsing (previous draft/version).
            replies.append(msg)

        if logger:
            logger.info("Fetched %d sender replies.", len(replies))

        return replies
    finally:
        client.disconnect()
