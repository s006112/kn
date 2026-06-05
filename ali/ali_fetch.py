#!/usr/bin/env python3
"""
ali_fetch.py

职责：
- 为 ALI pipeline 抓取及过滤 IMAP message。
- 执行 sender allowlist、ADMIN bypass 和 review-thread detection。
- 不负责 mark SEEN、review protocol parsing 或 content generation。

完整 fetch contract：
- 见 ali/README.md

Used by:
- ali_email.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from email import message_from_bytes
from email.policy import default as email_default_policy
from email.utils import getaddresses, parseaddr
from typing import Iterable, List

from dotenv import dotenv_values

from helper.helper_config import (
    configure_logging,
    load_env,
)  # type: ignore
from helper.utils_imap_client import ImapClient, RawFetchedRecord  # type: ignore
from helper.utils_imap_config import load_imap_config  # type: ignore
from helper.utils_imap_types import EmailMessage  # type: ignore

from ali.ali_parse import (
    REVIEW_SUBJECT_IMAP_QUERY,
    REVIEW_SUBJECT_PATTERN,
)  # review-thread detection

_ALLOWED_DOMAIN_SUFFIX = "@ampco.com.hk"
_REJECTED_SENDERS = {"ali@ampco.com.hk"}
_DOTENV_PATH = ROOT / ".env"


# 载入环境变量，让本模组可读取 dotenv 设定。
load_env()


# ---------------------------------------------------------------------
# 内部 helper
# ---------------------------------------------------------------------

def _is_allowed_sender(from_addr: str) -> bool:
    """允许内部审核人员，但排除 ALI 自身地址。"""
    sender = (from_addr or "").lower()
    return sender not in _REJECTED_SENDERS and sender.endswith(_ALLOWED_DOMAIN_SUFFIX)


def _should_bypass_admin(from_addr: str) -> bool:
    """
    判断是否跳过 ADMIN_USERNAME 邮件。
    - ALI_DEBUG_MODE=True：正常处理 ADMIN_USERNAME 邮件。
    - ALI_DEBUG_MODE=False：跳过来自 ADMIN_USERNAME 的邮件。
    即时读取 .env，让 poller 无需重启即可取得最新设定。
    """
    env_values = dotenv_values(_DOTENV_PATH)

    admin_addr = str(env_values.get("ADMIN_USERNAME", "")).strip().lower()
    if not admin_addr:
        return False

    debug_raw = str(env_values.get("ALI_DEBUG_MODE", "")).strip()
    debug_mode = True if debug_raw == "" else debug_raw.lower() == "true"

    return not debug_mode and (from_addr or "").lower() == admin_addr


def _build_client(logger, *, require_credentials: bool) -> tuple[ImapClient, str]:
    """根据环境设定建立并连接 IMAP client。"""
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


def _parse_address(header: str) -> tuple[str, str]:
    name, addr = parseaddr(header or "")
    return " ".join((name or "").replace('"', "").split()), (addr or "").strip()


def _raw_to_email_message(rec: RawFetchedRecord) -> EmailMessage:
    """
    将 raw record 解析为 EmailMessage。
    预期收件由审核人员转寄，因此 from_addr 应为审核人员地址。
    无效的寄送目标由 downstream guard 拒绝。
    """
    msg = message_from_bytes(rec.raw_bytes, policy=email_default_policy)
    from_name, from_addr = _parse_address(msg.get("From", ""))

    def _addr_list(header: str) -> List[str]:
        return [addr.strip() for _, addr in getaddresses([header or ""]) if addr.strip()]

    return EmailMessage(
        uid=rec.uid,
        message_id=msg.get("Message-ID", ""),
        from_addr=from_addr,
        to_addrs=_addr_list(msg.get("To", "")),
        cc_addrs=_addr_list(msg.get("Cc", "")),
        subject=msg.get("Subject", ""),
        body_text=msg.get_body(preferencelist=("plain",)).get_content()
        if msg.get_body(preferencelist=("plain",))
        else "",
        raw_bytes=rec.raw_bytes,
        from_name=from_name,
    )


def _fetch_records(
    client: ImapClient,
    folder: str,
    criteria: Iterable[str],
    limit: int | None = None,
) -> List[RawFetchedRecord]:
    """抓取符合条件的 record，但不标记为 SEEN。"""
    uids = client.search_uids(folder, list(criteria))
    if not uids:
        return []
    if limit is not None:
        uids = uids[:limit]
    return client.fetch_batch(folder, uids)


# ---------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------

def fetch_new_messages(max_messages: int = 10) -> List[EmailMessage]:
    """
    抓取 Phase 1 的 UNSEEN 邮件。
    在应用层排除 review thread，并由 pipeline 负责 mark-seen。
    """
    logger = configure_logging("ali_fetch")
    client, folder = _build_client(logger, require_credentials=True)

    try:
        records = _fetch_records(client, folder, ["UNSEEN"])
        messages: List[EmailMessage] = []

        for rec in records:
            email = _raw_to_email_message(rec)

            # ALI_DEBUG_MODE 明确关闭时，跳过设定的 IMAP user。
            if _should_bypass_admin(email.from_addr):
                logger.info(
                    "Bypassing message from %s uid=%s due to ALI_DEBUG_MODE=FALSE",
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

            # Phase 1 跳过 review thread。
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
    抓取 Phase 2 的 UNSEEN 回复，其 subject 必须符合保留的 "[ALI:v"
    review-thread namespace。由 pipeline 负责 mark-seen。
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
            # ALI_DEBUG_MODE 关闭时，跳过内部 IT/IMAP user。
            if _should_bypass_admin(email.from_addr):
                logger.info(
                    "Bypassing reply from %s uid=%s due to ALI_DEBUG_MODE=FALSE",
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
