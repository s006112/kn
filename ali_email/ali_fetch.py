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
- Marking messages as SEEN (done by pipeline after successful processing)

System assumptions (EXPLICIT):
- "[ALI:v" is a RESERVED subject namespace used exclusively for ALI review threads.
  It MUST NOT appear in any non-review subject lines.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from email import message_from_bytes
from email.policy import default as email_default_policy
from email.utils import parseaddr
from typing import Iterable, List

from helper.utils_config import (
    configure_logging,
    load_env,
    get_env_flag,
    get_env_str,
)  # type: ignore
from helper.utils_imap_client import ImapClient, RawFetchedRecord  # type: ignore
from helper.utils_imap_config import load_imap_config  # type: ignore
from helper.utils_imap_types import EmailMessage  # type: ignore

from ali_email.ali_mail_parse import (
    REVIEW_SUBJECT_IMAP_QUERY,
    REVIEW_SUBJECT_PATTERN,
)  # review-thread detection

_ALLOWED_DOMAIN_SUFFIX = "@ampco.com.hk"


# Load environment and derive debug-bypass behavior.
# When DEBUG_MODE is set to "false" in the environment, bypass any unread
# messages coming from the configured IMAP_USERNAME (e.g. kennyng@ampco.com.hk).
load_env()
_DEBUG_MODE = get_env_flag("DEBUG_MODE", default=True)
_KENNY_ADDR = get_env_str("IMAP_USERNAME", "").lower()


# ---------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------

def _is_allowed_sender(from_addr: str) -> bool:
    """
    Safety-critical guard: ONLY internal reviewers from @ampco.com.hk may enter
    the pipeline. All other senders MUST be rejected and moved to IMAP Trash.
    Do not remove.
    """
    return (from_addr or "").lower().endswith(_ALLOWED_DOMAIN_SUFFIX)


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

    This is the ONLY place where MIME parsing happens.

    Assumption:
    - All inbound emails are FORWARDED by a human reviewer.
    - Therefore, `from_addr` is expected to be the reviewer,
      NOT the original customer.

    Emails that violate this assumption are intentionally
    handled downstream (e.g. rejected by ali_send).
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
    limit: int | None = None,
) -> List[RawFetchedRecord]:
    """
    Search and fetch all matching records for given IMAP criteria.

    Note:
    - This function does NOT mark messages as SEEN.
    - Consumption semantics are handled by the pipeline layer.
    """
    uids = client.search_uids(folder, list(criteria))
    if not uids:
        return []
    if limit is not None:
        uids = uids[:limit]
    return client.fetch_batch(folder, uids)


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------

def fetch_new_messages(max_messages: int = 10) -> List[EmailMessage]:
    """
    Fetch brand-new inbound messages (Phase 1).

    Rules:
    - Only UNSEEN messages are fetched from IMAP.
    - Review-thread messages are EXCLUDED at the application layer
      using REVIEW_SUBJECT_PATTERN.
    - Messages are marked as SEEN by the pipeline AFTER successful processing.

    Design note:
    - Review-thread exclusion is intentionally enforced at the application layer
      (not via IMAP SUBJECT filters) to keep IMAP criteria minimal and robust
      across servers.
    """
    logger = configure_logging("ali_fetch")
    client, folder = _build_client(logger, require_credentials=True)

    try:
        records = _fetch_records(client, folder, ["UNSEEN"], limit=max_messages)
        messages: List[EmailMessage] = []

        for rec in records:
            email = _raw_to_email_message(rec)

            # If DEBUG_MODE is explicitly disabled, bypass messages from the
            # configured IMAP user (commonly the internal IT account).
            if not _DEBUG_MODE and (email.from_addr or "").lower() == _KENNY_ADDR:
                logger.info(
                    "Bypassing message from %s uid=%s due to DEBUG_MODE=FALSE",
                    email.from_addr,
                    rec.uid,
                )
                continue

            if not _is_allowed_sender(email.from_addr):
                logger.warning(
                    "Rejecting non-allowlisted sender uid=%s from=%s",
                    rec.uid,
                    email.from_addr,
                )
                client.move_message(folder, rec.uid, "Trash")
                continue

            # Skip review threads in Phase 1 (single source of truth)
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
    Fetch sender replies to existing review threads (Phase 2).

    Rules:
    - Only UNSEEN messages are fetched.
    - Subject MUST match the reserved review-thread marker.
    - Messages are marked as SEEN by the pipeline AFTER successful processing.

    Assumption:
    - REVIEW_SUBJECT_IMAP_QUERY ("[ALI:v") is a reserved namespace and uniquely
      identifies ALI review threads.
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
            # Skip internal IT/IMAP user when DEBUG_MODE is disabled
            if not _DEBUG_MODE and (email.from_addr or "").lower() == _KENNY_ADDR:
                logger.info(
                    "Bypassing reply from %s uid=%s due to DEBUG_MODE=FALSE",
                    email.from_addr,
                    rec.uid,
                )
                continue
            if not _is_allowed_sender(email.from_addr):
                logger.warning(
                    "Rejecting non-allowlisted sender uid=%s from=%s",
                    rec.uid,
                    email.from_addr,
                )
                client.move_message(folder, rec.uid, "Trash")
                continue
            replies.append(email)

        return replies

    finally:
        client.disconnect()
