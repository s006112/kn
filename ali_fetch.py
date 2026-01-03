#!/usr/bin/env python3
"""
ali_fetch.py

IMAP fetch orchestration layer.

Responsibilities:
- Define *what* messages to fetch (business rules).
- Convert raw IMAP records into internal EmailMessage objects.
- Delegate all IMAP protocol details, retries, SSL/TLS handling,
  and mailbox operations to helper utilities.

Non-responsibilities (intentionally delegated):
- IMAP connect / reconnect / retry logic
- UID FETCH parsing
- SSL / legacy TLS handling
- Marking seen / moving messages implementation details
"""

from __future__ import annotations

from email import message_from_bytes
from email.policy import default as email_default_policy
from email.utils import parseaddr
from typing import Iterable, List

from helper.utils_config import configure_logging  # type: ignore
from helper.utils_imap_client import ImapClient, RawFetchedRecord  # type: ignore
from helper.utils_imap_config import load_imap_config  # type: ignore
from helper.utils_imap_types import EmailMessage  # type: ignore

from ali_mail_parse import REVIEW_SUBJECT_IMAP_QUERY, REVIEW_SUBJECT_PATTERN  # review-thread detection


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------

def _build_client(logger, *, require_credentials: bool) -> tuple[ImapClient, str]:
    """
    Build and connect an ImapClient using environment configuration.

    Returns:
        (client, folder_name)

    Raises:
        RuntimeError if required IMAP credentials are missing.
    """
    cfg = load_imap_config(
        "IMAP_FOLDER",
        "INBOX",
        require_credentials=require_credentials,
    )
    if cfg is None:
        raise RuntimeError("IMAP configuration missing.")

    client = ImapClient(
        server=cfg.host,
        port=cfg.port,
        user=cfg.user,
        password=cfg.password,
        verify_ssl=cfg.verify_ssl,
        timeout=cfg.timeout,
        logger=logger,
    )
    client.connect()
    return client, cfg.folder


def _raw_to_email_message(rec: RawFetchedRecord) -> EmailMessage:
    """
    Convert a RawFetchedRecord into internal EmailMessage.

    This is the *only* place where MIME parsing happens.
    """
    msg = message_from_bytes(rec.raw_bytes, policy=email_default_policy)

    def _addr_list(header: str) -> List[str]:
        if not header:
            return []
        return [parseaddr(part)[1] for part in header.split(",") if part.strip()]

    return EmailMessage(
        uid=rec.uid,
        message_id=msg.get("Message-ID", ""),
        from_addr=parseaddr(msg.get("From", ""))[1],
        to_addrs=_addr_list(msg.get("To", "")),
        cc_addrs=_addr_list(msg.get("Cc", "")),
        subject=msg.get("Subject", ""),
        body_text=msg.get_body(preferencelist=("plain",)).get_content()
        if msg.get_body(preferencelist=("plain",))
        else "",
        raw_bytes=rec.raw_bytes,
    )


def _fetch_records(
    client: ImapClient,
    folder: str,
    criteria: Iterable[str],
) -> List[RawFetchedRecord]:
    """
    Search and fetch all matching records for given IMAP criteria.
    """
    uids = client.search_uids(folder, list(criteria))
    if not uids:
        return []
    return client.fetch_batch(folder, uids)


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def fetch_new_messages(max_messages: int = 10) -> List[EmailMessage]:
    """
    Fetch brand-new inbound messages (non-review threads).

    Rules:
    - Only UNSEEN messages.
    - Review-thread subjects are ignored here.
    - Messages are marked as SEEN once accepted.
    """
    logger = configure_logging("ali_fetch")
    client, folder = _build_client(logger, require_credentials=True)

    try:
        records = _fetch_records(client, folder, ["UNSEEN"])
        messages: List[EmailMessage] = []

        for rec in records:
            email = _raw_to_email_message(rec)

            # Skip review threads in Phase 1 (protocol-based, single source of truth)
            if REVIEW_SUBJECT_PATTERN.search(email.subject or ""):
                logger.debug(
                    "Skipping review-thread message uid=%s subject=%s",
                    rec.uid,
                    email.subject,
                )
                continue

            messages.append(email)

            if len(messages) >= max_messages:
                break

        return messages

    finally:
        client.disconnect()


def fetch_sender_replies() -> List[EmailMessage]:
    """
    Fetch sender replies to existing review threads.

    Rules:
    - Only UNSEEN messages.
    - Subject must match review-thread marker.
    - Messages are marked as SEEN after fetching.
    """
    logger = configure_logging("ali_fetch")
    client, folder = _build_client(logger, require_credentials=True)

    try:
        if not REVIEW_SUBJECT_IMAP_QUERY:
            return []

        records = _fetch_records(
            client,
            folder,
            ["UNSEEN", "SUBJECT", REVIEW_SUBJECT_IMAP_QUERY],
        )

        replies: List[EmailMessage] = []

        for rec in records:
            email = _raw_to_email_message(rec)
            replies.append(email)

        return replies

    finally:
        client.disconnect()
