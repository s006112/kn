#!/usr/bin/env python3
"""
utils_imap_ops.py

- 基於 ImapClient 提供 IMAP 相關高階動作
- 提供兩組 API：
  1) *_with_client：需要呼叫端提供已連線的 ImapClient（純邏輯，不管 connect/disconnect）
  2) 簡易版：append_to_imap_sent / mark_imap_message_seen 自己建 ImapClient、連線、呼叫 *with_client
"""

from __future__ import annotations

from email.message import EmailMessage as StdEmailMessage
from typing import Any

from utils_imap_config import load_imap_config  # type: ignore
from utils_imap_client import ImapClient  # type: ignore


# ------------------------------------------------------------
# Core: 使用現有 ImapClient 的版本（純邏輯，不管連線生命週期）
# ------------------------------------------------------------

def append_to_imap_sent_with_client(
    client: ImapClient,
    msg: StdEmailMessage,
    logger: Any = None,
) -> None:
    """
    使用已連線的 ImapClient，將郵件寫入「已寄信」資料夾。

    - 不讀 env（除了用 load_imap_config 取資料夾名稱）
    - 不呼叫 connect()/disconnect()
    """
    imap_cfg = load_imap_config("IMAP_SENT_FOLDER", "Sent")
    if imap_cfg is None:
        if logger:
            logger.debug("IMAP_* 未完整設定，略過 APPEND 至 Sent。")
        return

    try:
        client.append_raw(imap_cfg.folder, msg.as_bytes())
        if logger:
            logger.info("已將信件寫入 IMAP 資料夾 %s。", imap_cfg.folder)
    except Exception as exc:
        if logger:
            logger.warning(
                "IMAP APPEND 至 %s 失敗：%s",
                imap_cfg.folder,
                exc,
            )


def mark_imap_message_seen_with_client(
    client: ImapClient,
    uid: int,
    logger: Any = None,
) -> None:
    """
    使用已連線的 ImapClient，將指定 UID 標記為已讀。

    - 不讀 env（除了用 load_imap_config 取資料夾名稱）
    - 不呼叫 connect()/disconnect()
    """
    imap_cfg = load_imap_config("IMAP_FOLDER", "INBOX")
    if imap_cfg is None:
        if logger:
            logger.debug("IMAP_* 未完整設定，略過標記已讀。")
        return

    try:
        client.mark_seen(imap_cfg.folder, uid)
        if logger:
            logger.info("已將 UID %s 標記為已讀。", uid)
    except Exception as exc:
        if logger:
            logger.warning(
                "IMAP 標記 UID %s 為已讀失敗：%s",
                uid,
                exc,
            )


def move_imap_message_with_client(
    client: ImapClient,
    source_folder: str,
    uid: int,
    target_folder: str,
    logger: Any = None,
) -> bool:
    """
    使用已連線的 ImapClient，將指定 UID 由 source_folder 移至 target_folder。
    """
    try:
        client.move_message(source_folder, uid, target_folder)
        if logger:
            logger.info(
                "已將 UID %s 從 %s 移動至 %s。",
                uid,
                source_folder,
                target_folder,
            )
        return True
    except Exception as exc:
        if logger:
            logger.warning(
                "移動 UID %s 至 %s 失敗：%s",
                uid,
                target_folder,
                exc,
            )
        return False


# ------------------------------------------------------------
# Convenience wrappers：自己建 ImapClient 的版本
# ------------------------------------------------------------

def _build_imap_client_from_config(logger: Any) -> ImapClient | None:
    """
    用 IMAP_* env 建立 ImapClient。
    - 若 host/user/password 缺失，回傳 None，由呼叫端自己處理略過邏輯。
    - 不在這裡做 require_credentials 的強制檢查。
    """
    imap_cfg = load_imap_config("IMAP_FOLDER", "INBOX", require_credentials=False)
    if imap_cfg is None:
        if logger:
            logger.debug("IMAP_* 未完整設定，略過 IMAP 操作。")
        return None

    return ImapClient(
        server=imap_cfg.host,
        port=imap_cfg.port,
        user=imap_cfg.user,
        password=imap_cfg.password,
        verify_ssl=imap_cfg.verify_ssl,
        timeout=imap_cfg.timeout,
        logger=logger,
    )


def append_to_imap_sent(msg: StdEmailMessage, logger: Any) -> None:
    """
    傳統方便版 API：
    - 自己建 ImapClient，connect()
    - 呼叫 append_to_imap_sent_with_client
    - disconnect()
    """
    client = _build_imap_client_from_config(logger)
    if client is None:
        return

    try:
        client.connect()
    except Exception as exc:
        if logger:
            logger.warning("連線 IMAP 伺服器失敗，略過 APPEND 至 Sent：%s", exc)
        return

    try:
        append_to_imap_sent_with_client(client, msg, logger=logger)
    finally:
        client.disconnect()


def mark_imap_message_seen(uid: int, logger: Any) -> None:
    """
    傳統方便版 API：
    - 自己建 ImapClient，connect()
    - 呼叫 mark_imap_message_seen_with_client
    - disconnect()
    """
    client = _build_imap_client_from_config(logger)
    if client is None:
        return

    try:
        client.connect()
    except Exception as exc:
        if logger:
            logger.warning("連線 IMAP 伺服器失敗，略過標記已讀：%s", exc)
        return

    try:
        mark_imap_message_seen_with_client(client, uid, logger=logger)
    finally:
        client.disconnect()
