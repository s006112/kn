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


# add near patterns
SEQ_PATTERN = re.compile(r"^\*?\s*(?P<seq>\d+)\s+FETCH", re.IGNORECASE)
UID_PATTERN = re.compile(r"\bUID\s+(?P<uid>\d+)\b")
FLAGS_PATTERN = re.compile(r"\bFLAGS\s+\((?P<flags>[^)]*)\)")
INTERNALDATE_PATTERN = re.compile(r'\bINTERNALDATE\s+"?(?P<date>[^"]+)"?')

def parse_fetch_response(data: Sequence[object]) -> Iterable[RawFetchedRecord]:
    """
    Robustly parse imaplib UID FETCH results.

    Fix:
    - Servers may split attributes across multiple tuples for the same message.
    - Later tuples may contain FLAGS () or FLAGS with fewer keywords than earlier tuples.
    - We must NOT overwrite a richer flag set with a poorer one.
      => accumulate (union) flags across tuples.
    """

    by_uid: dict[int, dict] = {}
    by_seq: dict[int, dict] = {}
    seq_to_uid: dict[int, int] = {}

    def _new_entry() -> dict:
        return {"flags": [], "flags_set": set(), "internaldate": None, "raw_bytes": None}

    def _get_entry(uid: Optional[int], seq: Optional[int]) -> dict:
        if uid is not None:
            return by_uid.setdefault(uid, _new_entry())
        if seq is not None:
            return by_seq.setdefault(seq, _new_entry())
        return _new_entry()  # throwaway

    def _merge_flags(entry: dict, parsed: List[str]) -> None:
        # union, keep first-seen order
        if not parsed:
            return
        s = entry["flags_set"]
        out = entry["flags"]
        for f in parsed:
            if f and f not in s:
                s.add(f)
                out.append(f)

    for item in data:
        if not item or not isinstance(item, tuple):
            continue

        header_bytes, payload = item
        if not isinstance(header_bytes, (bytes, str)):
            continue

        header = header_bytes.decode("utf-8", errors="ignore")

        # seq
        seq: Optional[int] = None
        m_seq = SEQ_PATTERN.search(header)
        if m_seq:
            try:
                seq = int(m_seq.group("seq"))
            except Exception:
                seq = None

        # uid
        uid: Optional[int] = None
        m_uid = UID_PATTERN.search(header)
        if m_uid:
            try:
                uid = int(m_uid.group("uid"))
            except Exception:
                uid = None

        if uid is not None and seq is not None:
            seq_to_uid[seq] = uid

        entry = _get_entry(uid, seq)

        # FLAGS
        m_flags = FLAGS_PATTERN.search(header)
        if m_flags is not None:
            raw = (m_flags.group("flags") or "").strip()
            parsed_flags = raw.split() if raw else []
            _merge_flags(entry, parsed_flags)

        # INTERNALDATE (keep first non-empty)
        m_date = INTERNALDATE_PATTERN.search(header)
        if m_date and entry["internaldate"] is None:
            entry["internaldate"] = m_date.group("date")

        # BODY
        if isinstance(payload, (bytes, bytearray)) and entry["raw_bytes"] is None:
            entry["raw_bytes"] = bytes(payload)

    # Promote seq entries into uid entries when we can map seq -> uid
    for seq, entry in list(by_seq.items()):
        uid = seq_to_uid.get(seq)
        if uid is None:
            continue
        dst = by_uid.setdefault(uid, _new_entry())
        _merge_flags(dst, entry.get("flags", []))
        if dst["internaldate"] is None and entry.get("internaldate") is not None:
            dst["internaldate"] = entry["internaldate"]
        if dst["raw_bytes"] is None and entry.get("raw_bytes") is not None:
            dst["raw_bytes"] = entry["raw_bytes"]

    # Emit only complete records
    for uid, entry in by_uid.items():
        if entry["raw_bytes"] is None:
            continue
        yield RawFetchedRecord(
            uid=uid,
            flags=entry["flags"] or [],
            internaldate=entry["internaldate"],
            raw_bytes=entry["raw_bytes"],
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

    # -------------- append / flags --------------

    def append_raw(self, folder: str, raw_bytes: bytes) -> None:
        """
        將一封完整 MIME 郵件（raw_bytes）追加寫入指定資料夾。
        """
        assert self._conn is not None
        encoded_name = self._encode_folder(folder)
        status, resp = self._conn.append(encoded_name, None, None, raw_bytes)
        if status != "OK":
            raise RuntimeError(f"APPEND to {folder!r} failed: {status} {resp}")

    def mark_seen(self, folder: str, uid: int) -> None:
        """
        對指定 folder + UID 加上 \\Seen。
        """
        assert self._conn is not None
        encoded_name = self._encode_folder(folder)
        status, _ = self._conn.select(encoded_name, readonly=False)
        if status != "OK":
            raise RuntimeError(
                f"Unable to select folder {folder!r} for mark_seen: {status}"
            )
        uid_str = str(uid)
        status, resp = self._conn.uid("STORE", uid_str, "+FLAGS", r"(\Seen)")
        if status != "OK":
            raise RuntimeError(
                f"Marking UID {uid_str} as \\Seen failed: {status} {resp}"
            )

    def move_message(self, source_folder: str, uid: int, target_folder: str) -> None:
        """
        移動指定 UID 至目標資料夾，若伺服器不支援 MOVE 則改用 COPY+DELETE。
        """
        assert self._conn is not None
        encoded_source = self._encode_folder(source_folder)
        status, _ = self._conn.select(encoded_source, readonly=False)
        if status != "OK":
            raise RuntimeError(f"Unable to select folder {source_folder!r} for move.")

        uid_str = str(uid)
        encoded_target = self._encode_folder(target_folder)
        try:
            status, resp = self._conn.uid("MOVE", uid_str, encoded_target)
        except imaplib.IMAP4.error:
            status, resp = "NO", None
        if status == "OK":
            return

        status, resp = self._conn.uid("COPY", uid_str, encoded_target)
        if status != "OK":
            raise RuntimeError(
                f"Copying UID {uid_str} to {target_folder!r} failed: {status} {resp}"
            )
        status, resp = self._conn.uid("STORE", uid_str, "+FLAGS", r"(\Deleted)")
        if status != "OK":
            raise RuntimeError(
                f"Marking UID {uid_str} as \\Deleted failed: {status} {resp}"
            )
        self._conn.expunge()

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
