#!/usr/bin/env python3
"""
Reusable IMAP utility module.

Extracted from 00_imap.py:
- Secure IMAP connection
- UTF-7 mailbox encoding/decoding
- Folder listing
- UID search
- Batch FETCH with retry/reconnect
- Return raw MIME bytes and metadata

Purpose:
- Provide a clean backend for both the old 00_imap exporter
- And new email event parsing (email.py)
"""

from __future__ import annotations
import os
import ssl
import time
import socket
import imaplib
from typing import List, Optional, Sequence, Iterable
from dataclasses import dataclass
from email import message_from_bytes

# ---------------------------------------------------------------------
# UTF-7 helpers (direct extraction from your original code)
# ---------------------------------------------------------------------

import base64
import binascii
import re

LIST_RE = re.compile(r'^\((?P<flags>.*?)\)\s+"(?P<delimiter>.*?)"\s+(?P<name>.*)$')
ATOM_SPECIALS = set('(){ %*"\\]')
SYSTEM_FOLDERS = {"junk", "trash", "drafts", "spam"}
UID_PATTERN = re.compile(r"UID (?P<uid>\d+)")
FLAGS_PATTERN = re.compile(r"FLAGS \((?P<flags>[^)]*)\)")
INTERNALDATE_PATTERN = re.compile(r'INTERNALDATE "?(?P<date>[^"]+)"?')


def decode_imap_utf7(value: str) -> str:
    result = []
    i = 0
    while i < len(value):
        if value[i] == "&":
            j = value.find("-", i)
            if j == -1:
                j = len(value)
            encoded = value[i+1:j]
            if not encoded:
                result.append("&")
            else:
                padding = (-len(encoded)) % 4
                encoded_bytes = (encoded + "=" * padding).replace(",", "/").encode("ascii")
                try:
                    decoded = base64.b64decode(encoded_bytes).decode("utf-16-be")
                except Exception:
                    result.append(encoded)
                else:
                    result.append(decoded)
            i = j + 1
        else:
            result.append(value[i])
            i += 1
    return "".join(result)


def encode_imap_utf7(value: str) -> str:
    result = []
    buf = []

    def flush():
        if not buf:
            return
        chunk = "".join(buf).encode("utf-16-be")
        encoded = base64.b64encode(chunk).decode("ascii").replace("/", ",").rstrip("=")
        result.append(f"&{encoded}-")
        buf.clear()

    for c in value:
        code = ord(c)
        if 0x20 <= code <= 0x7E:
            flush()
            if c == "&":
                result.append("&-")
            else:
                result.append(c)
        else:
            buf.append(c)
    flush()
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
    m = LIST_RE.match(text)
    if not m:
        return None
    name = m.group("name")
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    name = name.replace('\\"', '"').replace("\\\\", "\\")
    try:
        return decode_imap_utf7(name)
    except Exception:
        return name


# ---------------------------------------------------------------------
# SSL builder
# ---------------------------------------------------------------------

def build_ssl_context(verify: bool, legacy: bool = False) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if legacy:
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        except ssl.SSLError:
            pass
        if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    return ctx


# ---------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------

@dataclass
class ImapFetchedMessage:
    uid: int
    flags: List[str]
    internaldate: Optional[str]
    raw_bytes: bytes


def parse_fetch_response(data: Sequence[object]) -> Iterable[ImapFetchedMessage]:
    for item in data:
        if not item or not isinstance(item, tuple):
            continue
        header_bytes, body_bytes = item
        if not isinstance(header_bytes, (bytes, str)) or not isinstance(body_bytes, (bytes, bytearray)):
            continue

        header = header_bytes.decode("utf-8", errors="ignore")
        uid_match = UID_PATTERN.search(header)
        if not uid_match:
            continue
        uid = int(uid_match.group("uid"))

        flags_match = FLAGS_PATTERN.search(header)
        flags = []
        if flags_match and flags_match.group("flags"):
            flags = [flag.strip() for flag in flags_match.group("flags").split()]

        date_match = INTERNALDATE_PATTERN.search(header)
        internaldate = date_match.group("date") if date_match else None

        yield ImapFetchedMessage(uid=uid, flags=flags, internaldate=internaldate, raw_bytes=body_bytes)


# ---------------------------------------------------------------------
# Core IMAP client wrapper
# ---------------------------------------------------------------------

class ImapClient:
    """
    Reusable IMAP wrapper:
    - connect / reconnect
    - list folders
    - search by date/ALL
    - fetch messages by UID batch
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
    ):
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout

        self.logger = logger
        self.connection: Optional[imaplib.IMAP4_SSL] = None
        self._ctx = None
        self._legacy = False

    # --------------------------------------------
    # connection
    # --------------------------------------------
    def connect(self):
        self._ctx = build_ssl_context(self.verify_ssl, legacy=self._legacy)
        try:
            if self.logger:
                self.logger.info(f"IMAP connect {self.server}:{self.port} as {self.user}")

            self.connection = imaplib.IMAP4_SSL(
                self.server,
                self.port,
                ssl_context=self._ctx,
                timeout=self.timeout,
            )
            self.connection.login(self.user, self.password)
        except ssl.SSLError as exc:
            if "dh key too small" in str(exc).lower() and not self._legacy:
                if self.logger:
                    self.logger.warning("Retrying IMAP with legacy TLS settings.")
                self._legacy = True
                return self.connect()
            raise
        return self

    def disconnect(self):
        if self.connection:
            try:
                self.connection.logout()
            except Exception:
                pass
            self.connection = None

    def reconnect(self):
        if self.logger:
            self.logger.debug("IMAP reconnect")
        self.disconnect()
        self.connect()

    # --------------------------------------------
    # folder list
    # --------------------------------------------
    def list_folders(self, include_system=False) -> List[str]:
        assert self.connection is not None

        status, data = self.connection.list()
        if status != "OK":
            raise RuntimeError("IMAP LIST failed")

        folders = []
        for entry in data:
            name = decode_mailbox(entry)
            if not name:
                continue
            if not include_system:
                parts = re.split(r"[/\\]", name.lower())
                if any(p in SYSTEM_FOLDERS for p in parts if p):
                    continue
            folders.append(name)
        folders.sort()
        return folders

    # --------------------------------------------
    # search
    # --------------------------------------------

    def search_uids(self, folder: str, criteria: Sequence[str]) -> List[int]:
        self._select(folder)
        status, data = self.connection.uid("SEARCH", None, *criteria)
        if status != "OK" or not data or not data[0]:
            return []
        raw = data[0].decode().split()
        return sorted(set(int(uid) for uid in raw))

    # --------------------------------------------
    # fetch (with retry)
    # --------------------------------------------

    UID_BATCH_RETRY_LIMIT = 4
    BACKOFF = 2

    def fetch_batch(self, folder: str, uids: Sequence[int]) -> List[ImapFetchedMessage]:
        assert self.connection is not None

        uid_set = ",".join(str(u) for u in uids)
        attempt = 0
        needs_reselect = False

        while True:
            attempt += 1
            if attempt > 1 or needs_reselect:
                try:
                    self._select(folder)
                except Exception:
                    if attempt >= self.UID_BATCH_RETRY_LIMIT:
                        raise
                    time.sleep(self.BACKOFF ** attempt)
                    continue
                needs_reselect = False

            try:
                status, data = self.connection.uid(
                    "FETCH",
                    uid_set,
                    "(BODY.PEEK[] FLAGS INTERNALDATE UID)"
                )
            except (imaplib.IMAP4.abort, imaplib.IMAP4.error, socket.timeout, OSError):
                if attempt >= self.UID_BATCH_RETRY_LIMIT:
                    raise
                self.reconnect()
                needs_reselect = True
                time.sleep(self.BACKOFF ** attempt)
                continue

            if status != "OK" or not data:
                if attempt >= self.UID_BATCH_RETRY_LIMIT:
                    raise RuntimeError("Invalid FETCH response")
                needs_reselect = True
                time.sleep(self.BACKOFF ** attempt)
                continue

            break

        return list(self._parse_fetch_response(data))

    # --------------------------------------------
    # helpers
    # --------------------------------------------

    def _select(self, folder: str):
        name = self._encode_folder(folder)
        status, _ = self.connection.select(name, readonly=True)
        if status != "OK":
            raise RuntimeError(f"Cannot select folder {folder}")

    def _encode_folder(self, folder: str) -> str:
        try:
            folder.encode("ascii")
            return quote_mailbox(folder)
        except UnicodeEncodeError:
            return quote_mailbox(encode_imap_utf7(folder))

    def _parse_fetch_response(self, data: Sequence[object]) -> Iterable[ImapFetchedMessage]:
        return parse_fetch_response(data)
