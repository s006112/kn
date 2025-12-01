#!/usr/bin/env python3
"""
- UTF-7 收發資料夾名稱
- SSL context（含 legacy TLS）
- 列出資料夾
- UID SEARCH
- FETCH + 重試 + 重新連線
- 解析 FETCH 回應為 raw bytes 訊息
"""

from __future__ import annotations
import base64
import binascii
import re
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

import imaplib
from email import message_from_bytes
import mailbox

# ---------------------------------------------------------------------
# 常數
# ---------------------------------------------------------------------

SYSTEM_FOLDERS = {"junk", "trash", "drafts", "spam"}

UID_BATCH_RETRY_LIMIT = 4
UID_BATCH_RETRY_BACKOFF_BASE = 2


# ---------------------------------------------------------------------
# UTF-7 / mailbox helpers（從原始 00_imap 抽出）
# ---------------------------------------------------------------------

LIST_RE = re.compile(r'^\((?P<flags>.*?)\)\s+"(?P<delimiter>.*?)"\s+(?P<name>.*)$')
ATOM_SPECIALS = set('(){ %*"\\]')


def decode_imap_utf7(value: str) -> str:
    result: List[str] = []
    i = 0
    while i < len(value):
        if value[i] == "&":
            j = value.find("-", i)
            if j == -1:
                j = len(value)
            encoded = value[i + 1 : j]
            if not encoded:
                result.append("&")
            else:
                padding = (-len(encoded)) % 4
                encoded_bytes = (encoded + "=" * padding).replace(",", "/").encode(
                    "ascii"
                )
                try:
                    decoded = base64.b64decode(encoded_bytes).decode("utf-16-be")
                except (binascii.Error, UnicodeDecodeError):
                    result.append(encoded)
                else:
                    result.append(decoded)
            i = j + 1
        else:
            result.append(value[i])
            i += 1
    return "".join(result)


def encode_imap_utf7(value: str) -> str:
    result: List[str] = []
    buffer: List[str] = []

    def flush_buffer() -> None:
        if not buffer:
            return
        chunk = "".join(buffer).encode("utf-16-be")
        encoded = (
            base64.b64encode(chunk).decode("ascii").replace("/", ",").rstrip("=")
        )
        result.append(f"&{encoded}-")
        buffer.clear()

    for char in value:
        code = ord(char)
        if 0x20 <= code <= 0x7E:
            flush_buffer()
            if char == "&":
                result.append("&-")
            else:
                result.append(char)
        else:
            buffer.append(char)
    flush_buffer()
    return "".join(result)


def quote_mailbox(name: str) -> str:
    if not name:
        return '""'
    if any(ch in ATOM_SPECIALS or ch.isspace() for ch in name):
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return name


def decode_mailbox(line: bytes) -> Optional[str]:
    if not line:
        return None
    text = line.decode("utf-8", errors="ignore")
    match = LIST_RE.match(text)
    if not match:
        return None
    name = match.group("name")
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    name = name.replace('\\"', '"').replace("\\\\", "\\")
    try:
        return decode_imap_utf7(name)
    except (UnicodeError, ValueError):
        return name


# ---------------------------------------------------------------------
# SSL context
# ---------------------------------------------------------------------

def build_ssl_context(verify: bool, legacy: bool = False) -> ssl.SSLContext:
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if legacy:
        try:
            context.set_ciphers("DEFAULT@SECLEVEL=1")
        except ssl.SSLError:
            pass
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            context.options |= ssl.OP_LEGACY_SERVER_CONNECT
    return context


# ---------------------------------------------------------------------
# FETCH 結果結構
# ---------------------------------------------------------------------

@dataclass
class RawFetchedRecord:
    uid: int
    flags: List[str]
    internaldate: Optional[str]
    raw_bytes: bytes


UID_PATTERN = re.compile(r"UID (?P<uid>\d+)")
FLAGS_PATTERN = re.compile(r"FLAGS \((?P<flags>[^)]*)\)")
INTERNALDATE_PATTERN = re.compile(r'INTERNALDATE "?(?P<date>[^"]+)"?')


def parse_fetch_response(data: Sequence[object]) -> Iterable[RawFetchedRecord]:
    """
    只做 IMAP RAW 轉為 (uid, flags, internaldate, raw_bytes)
    不做 mailbox.mboxMessage，讓 00_imap 自行決定怎麼寫入。
    """
    for item in data:
        if not item or not isinstance(item, tuple):
            continue
        header_bytes, message_bytes = item
        if not isinstance(header_bytes, (bytes, str)) or not isinstance(
            message_bytes, (bytes, bytearray)
        ):
            continue
        header = header_bytes.decode("utf-8", errors="ignore")
        uid_match = UID_PATTERN.search(header)
        if not uid_match:
            continue
        uid = int(uid_match.group("uid"))
        flags_match = FLAGS_PATTERN.search(header)
        flags: List[str] = []
        if flags_match and flags_match.group("flags"):
            flags = [flag.strip() for flag in flags_match.group("flags").split()]
        date_match = INTERNALDATE_PATTERN.search(header)
        internaldate = date_match.group("date") if date_match else None
        yield RawFetchedRecord(
            uid=uid,
            flags=flags,
            internaldate=internaldate,
            raw_bytes=bytes(message_bytes),
        )


# ---------------------------------------------------------------------
# 高階 ImapClient 封裝
# ---------------------------------------------------------------------

class ImapClient:
    """
    封裝：
    - connect / disconnect / reconnect
    - list_folders(include_system)
    - search_uids(folder, criteria)
    - fetch_batch(folder, uid_batch) -> RawFetchedRecord[]
    """

    def __init__(
        self,
        server: str,
        port: int,
        user: str,
        password: str,
        verify_ssl: bool = True,
        timeout: int = 300,
        logger=None,
    ) -> None:
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.logger = logger

        self._ctx: Optional[ssl.SSLContext] = None
        self._using_legacy = False
        self._conn: Optional[imaplib.IMAP4_SSL] = None

    # -------------- connection --------------

    def connect(self) -> "ImapClient":
        self._ctx = build_ssl_context(self.verify_ssl, legacy=self._using_legacy)
        try:
            if self.logger:
                self.logger.info(
                    "Connecting to %s:%s as %s",
                    self.server,
                    self.port,
                    self._mask_identifier(self.user),
                )
            self._conn = imaplib.IMAP4_SSL(
                self.server,
                self.port,
                ssl_context=self._ctx,
                timeout=self.timeout,
            )
            self._conn.login(self.user, self.password)
        except ssl.SSLError as exc:
            msg = str(exc)
            if "dh key too small" in msg.lower() and not self._using_legacy:
                if self.logger:
                    self.logger.warning(
                        "Server requires weak DH parameters; retrying with legacy TLS."
                    )
                self._using_legacy = True
                return self.connect()
            raise
        if self._using_legacy and self.logger:
            self.logger.warning(
                "Connected using legacy TLS mode; prefer updating server DH parameters."
            )
        return self

    def disconnect(self) -> None:
        if self._conn is not None:
            try:
                self._conn.logout()
            except Exception:
                pass
            self._conn = None

    def reconnect(self) -> None:
        if self.logger:
            self.logger.debug("Reconnecting to IMAP server after error.")
        self.disconnect()
        self.connect()

    # -------------- folders / search --------------

    def list_folders(self, include_system: bool = False) -> List[str]:
        assert self._conn is not None
        status, data = self._conn.list()
        if status != "OK" or data is None:
            raise RuntimeError("Unable to retrieve folder list.")
        folders: List[str] = []
        for entry in data:
            folder = decode_mailbox(entry)
            if not folder:
                continue
            if not include_system:
                parts = re.split(r"[/\\]", folder.lower())
                if any(part in SYSTEM_FOLDERS for part in parts if part):
                    if self.logger:
                        self.logger.debug("Skipping system folder %s", folder)
                    continue
            folders.append(folder)
        folders.sort()
        return folders

    def search_uids(self, folder: str, criteria: Sequence[str]) -> List[int]:
        self._select(folder)
        assert self._conn is not None
        status, data = self._conn.uid("SEARCH", None, *criteria)
        if status != "OK" or not data or not data[0]:
            return []
        raw_uids = data[0].decode().split()
        return sorted(set(int(uid) for uid in raw_uids))

    # -------------- fetch w/ retry --------------

    def fetch_batch(self, folder: str, uid_batch: Sequence[int]) -> List[RawFetchedRecord]:
        assert self._conn is not None
        uid_set = ",".join(str(uid) for uid in uid_batch)
        attempt = 0
        needs_reselect = False

        while True:
            attempt += 1
            if needs_reselect or attempt > 1:
                try:
                    self._select(folder)
                except RuntimeError as exc:
                    if attempt >= UID_BATCH_RETRY_LIMIT:
                        raise
                    delay = UID_BATCH_RETRY_BACKOFF_BASE ** attempt
                    if self.logger:
                        self.logger.warning(
                            "Folder %s: reselect failed (%s); retrying after %ss",
                            folder,
                            exc,
                            delay,
                        )
                    time.sleep(delay)
                    continue
                needs_reselect = False

            try:
                status, data = self._conn.uid(
                    "FETCH",
                    uid_set,
                    "(BODY.PEEK[] FLAGS INTERNALDATE UID)",
                )
            except (
                imaplib.IMAP4.abort,
                imaplib.IMAP4.error,
                socket.timeout,
                OSError,
            ) as exc:
                if attempt >= UID_BATCH_RETRY_LIMIT:
                    raise RuntimeError(f"Fetch failed for {folder}: {exc}") from exc
                delay = UID_BATCH_RETRY_BACKOFF_BASE ** attempt
                if self.logger:
                    self.logger.warning(
                        "Fetch retry %d/%d for %s after error: %s (sleep %ss)",
                        attempt,
                        UID_BATCH_RETRY_LIMIT - 1,
                        folder,
                        exc,
                        delay,
                    )
                self.reconnect()
                needs_reselect = True
                time.sleep(delay)
                continue

            if status != "OK" or not data:
                if attempt >= UID_BATCH_RETRY_LIMIT:
                    raise RuntimeError(f"Invalid fetch response for {folder}.")
                delay = UID_BATCH_RETRY_BACKOFF_BASE ** attempt
                needs_reselect = True
                time.sleep(delay)
                continue

            break

        return list(parse_fetch_response(data))

    # -------------- internal helpers --------------

    def _select(self, folder: str) -> None:
        assert self._conn is not None
        encoded_name = self._encode_folder(folder)
        status, _ = self._conn.select(encoded_name, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Unable to select folder {folder}.")

    @staticmethod
    def _encode_folder(folder: str) -> str:
        try:
            folder.encode("ascii")
            return quote_mailbox(folder)
        except UnicodeEncodeError:
            return quote_mailbox(encode_imap_utf7(folder))

    @staticmethod
    def _mask_identifier(identifier: str) -> str:
        if not identifier:
            return ""
        if len(identifier) <= 4:
            return "*" * len(identifier)
        return identifier[:2] + "*" * (len(identifier) - 4) + identifier[-2:]
