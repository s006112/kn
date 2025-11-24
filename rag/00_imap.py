#!/usr/bin/env python3
"""Export mailboxes over IMAP into local mbox archives with incremental sync support."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import logging
import os
import re
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from email import message_from_bytes
import mailbox
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import imaplib

from dotenv import load_dotenv


DEFAULT_SERVER = "mail.ampco.com.hk"
DEFAULT_PORT = 993
DEFAULT_TIMEOUT = 300
DEFAULT_SINCE_DATE = "2025-09-20"
DEFAULT_OUT_DIR = Path("data") / "raw" / "mbox"
DEFAULT_STATE_PATH = Path(".state") / "imap_state.json"
DEFAULT_CHUNK_SIZE = 100
SYSTEM_FOLDERS = {"junk", "trash", "drafts", "spam"}
UID_BATCH_RETRY_LIMIT = 4
UID_BATCH_RETRY_BACKOFF_BASE = 2


class ExportError(RuntimeError):
    """Raised when the export process fails."""


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export IMAP folders into local mbox files."
    )
    parser.add_argument(
        "--since",
        type=str,
        default=DEFAULT_SINCE_DATE,
        help="Lower bound date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--before", type=str, help="Upper bound date (YYYY-MM-DD, exclusive)."
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Perform a full export ignoring stored state.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory for mbox files (default: {DEFAULT_OUT_DIR}).",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help=f"Path to sync state JSON (default: {DEFAULT_STATE_PATH}).",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Max UID fetch chunk size (default: %(default)s).",
    )
    parser.add_argument(
        "--include-system",
        action="store_true",
        help="Include system folders (Junk/Trash/Drafts/Spam).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable SSL certificate verification (warning: insecure).",
    )
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("imap_export")
    if not verbose:
        logger.setLevel(logging.INFO)
    logger.debug("Verbose logging enabled.")
    return logger


def load_credentials(logger: logging.Logger) -> Tuple[str, str]:
    load_dotenv()
    user = os.getenv("IMAP_USER")
    password = os.getenv("IMAP_PASSWORD")
    if not user or not password:
        logger.error("Missing IMAP_USER or IMAP_PASSWORD in environment.")
        raise ExportError("Missing IMAP credentials.")
    logger.debug("Credentials loaded from environment.")
    return user, password


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


def mask_identifier(identifier: str) -> str:
    if not identifier:
        return ""
    if len(identifier) <= 4:
        return "*" * len(identifier)
    return identifier[:2] + "*" * (len(identifier) - 4) + identifier[-2:]


LIST_RE = re.compile(r'^\((?P<flags>.*?)\)\s+"(?P<delimiter>.*?)"\s+(?P<name>.*)$')


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


ATOM_SPECIALS = set('(){ %*"\\]')


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


def to_search_date(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ExportError(f"Invalid date '{value}': expected YYYY-MM-DD.") from exc
    return parsed.strftime("%d-%b-%Y")


def build_search_criteria(
    since: Optional[str], before: Optional[str]
) -> List[str]:
    criteria: List[str] = ["ALL"]
    if since:
        criteria.append(f"SINCE {since}")
    if before:
        criteria.append(f"BEFORE {before}")
    return criteria


def chunked(sequence: Sequence[int], size: int) -> Iterable[List[int]]:
    for index in range(0, len(sequence), size):
        yield sequence[index : index + size]


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


@dataclass
class SyncState:
    path: Path
    data: Dict[str, int]

    @classmethod
    def load(cls, path: Path) -> "SyncState":
        if path.exists():
            try:
                content = json.loads(path.read_text(encoding="utf-8"))
                folders = content.get("folders", {})
                if isinstance(folders, dict):
                    sanitized = {
                        str(folder): int(uid) for folder, uid in folders.items()
                    }
                else:
                    sanitized = {}
            except (json.JSONDecodeError, OSError, ValueError):
                sanitized = {}
        else:
            sanitized = {}
        return cls(path=path, data=sanitized)

    def last_uid(self, folder: str) -> Optional[int]:
        return self.data.get(folder)

    def update(self, folder: str, uid: int) -> None:
        self.data[folder] = uid

    def save(self) -> None:
        ensure_parent(self.path)
        payload = {"folders": self.data}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class ImapExporter:
    def __init__(
        self,
        server: str,
        port: int,
        user: str,
        password: str,
        out_dir: Path,
        state: SyncState,
        chunk_size: int,
        include_system: bool,
        full: bool,
        since: Optional[str],
        before: Optional[str],
        verify_ssl: bool,
        logger: logging.Logger,
    ) -> None:
        self.server = server
        self.port = port
        self.user = user
        self.password = password
        self.out_dir = out_dir
        self.state = state
        self.chunk_size = max(1, chunk_size)
        self.include_system = include_system
        self.full = full
        self.since = since
        self.before = before
        self.verify_ssl = verify_ssl
        self.timeout = DEFAULT_TIMEOUT
        self.logger = logger
        self.connection: Optional[imaplib.IMAP4_SSL] = None
        self._ssl_context: Optional[ssl.SSLContext] = None
        self._using_legacy_tls = False
        self.summary_outputs: List[Path] = []
        self.summary_messages = 0
        self.summary_folders = 0
        self.export_date = datetime.now().strftime("%Y%m%d")
        self._shared_output_path: Optional[Path] = None
        self._mbox_prepared = False

    def run(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._ssl_context = build_ssl_context(self.verify_ssl)
        if not self.verify_ssl:
            self.logger.warning(
                "SSL verification disabled via --insecure. Use only for testing."
            )
        self._connect()

        try:
            folders = self._list_folders()
            if not folders:
                self.logger.warning("No folders discovered.")
                return
            self.summary_folders = len(folders)
            for folder in folders:
                try:
                    count = self._export_folder(folder)
                    self.summary_messages += count
                except ExportError as exc:
                    self.logger.error("Folder '%s' skipped: %s", folder, exc)
        finally:
            self._disconnect()

        self.state.save()
        self._log_summary()

    def _connect(self) -> None:
        assert self._ssl_context is not None
        try:
            self.logger.info(
                "Connecting to %s:%s as %s",
                self.server,
                self.port,
                mask_identifier(self.user),
            )
            self.connection = imaplib.IMAP4_SSL(
                self.server,
                self.port,
                ssl_context=self._ssl_context,
                timeout=self.timeout,
            )
            self.connection.login(self.user, self.password)
        except ssl.SSLError as exc:
            message = str(exc)
            if "dh key too small" in message.lower() and not self._using_legacy_tls:
                self.logger.warning(
                    "Server requires weak DH parameters; retrying connection with legacy TLS settings."
                )
                self._ssl_context = build_ssl_context(self.verify_ssl, legacy=True)
                self._using_legacy_tls = True
                self._connect()
                return
            raise ExportError(f"SSL error: {exc}") from exc
        except (imaplib.IMAP4.error, socket.timeout, OSError) as exc:
            raise ExportError(f"Failed to connect to IMAP server: {exc}") from exc
        else:
            if self._using_legacy_tls:
                self.logger.warning(
                    "Connected using legacy TLS mode; prefer updating server DH parameters."
                )

    def _disconnect(self) -> None:
        if self.connection is not None:
            try:
                self.connection.logout()
            except Exception:
                pass
            finally:
                self.connection = None

    def _reconnect(self) -> None:
        self.logger.debug("Reconnecting to IMAP server after error.")
        self._disconnect()
        self._connect()

    def _list_folders(self) -> List[str]:
        assert self.connection is not None
        status, data = self.connection.list()
        if status != "OK" or data is None:
            raise ExportError("Unable to retrieve folder list.")
        folders: List[str] = []
        for entry in data:
            folder = decode_mailbox(entry)
            if not folder:
                continue
            if not self.include_system:
                parts = re.split(r"[/\\]", folder.lower())
                if any(part in SYSTEM_FOLDERS for part in parts if part):
                    self.logger.debug("Skipping system folder %s", folder)
                    continue
            folders.append(folder)
        folders.sort()
        self.logger.info("Discovered %d folders to process.", len(folders))
        return folders

    def _ensure_folder_selected(self, folder: str) -> None:
        assert self.connection is not None
        encoded_name = self._encode_folder(folder)
        status, _ = self.connection.select(encoded_name, readonly=True)
        if status != "OK":
            raise ExportError(f"Unable to reselect folder {folder}.")

    def _export_folder(self, folder: str) -> int:
        assert self.connection is not None
        encoded_name = self._encode_folder(folder)
        status, _ = self.connection.select(encoded_name, readonly=True)
        if status != "OK":
            raise ExportError("Unable to select folder.")

        criteria = build_search_criteria(self.since, self.before)
        status, data = self.connection.uid("SEARCH", None, *criteria)
        if status != "OK" or not data or not data[0]:
            self.logger.info("Folder %s: no messages match criteria.", folder)
            return 0
        raw_uids = data[0].decode().split()
        uids = sorted(set(int(uid) for uid in raw_uids))
        if not self.full:
            last_uid = self.state.last_uid(folder)
            if last_uid:
                uids = [uid for uid in uids if uid > last_uid]
        if not uids:
            self.logger.info("Folder %s: no new messages after filtering.", folder)
            return 0

        self.logger.info(
            "Folder %s: %d messages to export (chunk size %d).",
            folder,
            len(uids),
            self.chunk_size,
        )
        output_path = self._resolve_output_path(folder)
        if not self._mbox_prepared:
            ensure_parent(output_path)
            if self.full and output_path.exists():
                output_path.unlink()
            self._mbox_prepared = True

        written = 0
        max_uid = 0
        mbox = mailbox.mbox(output_path, create=True)
        try:
            mbox.lock()
            for batch in chunked(uids, self.chunk_size):
                messages = self._fetch_batch(folder, batch)
                for message in messages:
                    mbox.add(message.message)
                    written += 1
                    max_uid = max(max_uid, message.uid)
                self.logger.debug(
                    "Folder %s: fetched %d/%d so far.",
                    folder,
                    written,
                    len(uids),
                )
            mbox.flush()
        finally:
            try:
                mbox.unlock()
            except Exception:
                pass
            mbox.close()

        if written:
            self.state.update(folder, max_uid)
            resolved_output = output_path.resolve()
            if resolved_output not in self.summary_outputs:
                self.summary_outputs.append(resolved_output)
            self.logger.info(
                "Folder %s: written %d messages to %s.",
                folder,
                written,
                output_path.resolve(),
            )
        return written

    def _fetch_batch(
        self, folder: str, uid_batch: Sequence[int]
    ) -> List["FetchedMessage"]:
        assert self.connection is not None
        uid_set = ",".join(str(uid) for uid in uid_batch)
        attempt = 0
        needs_reselect = False
        while True:
            attempt += 1
            if needs_reselect or attempt > 1:
                try:
                    self._ensure_folder_selected(folder)
                except ExportError as exc:
                    if attempt >= UID_BATCH_RETRY_LIMIT:
                        raise
                    delay = UID_BATCH_RETRY_BACKOFF_BASE ** attempt
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
                status, data = self.connection.uid(
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
                    raise ExportError(f"Fetch failed for {folder}: {exc}") from exc
                delay = UID_BATCH_RETRY_BACKOFF_BASE ** attempt
                self.logger.warning(
                    "Fetch retry %d/%d for %s after error: %s (sleep %ss)",
                    attempt,
                    UID_BATCH_RETRY_LIMIT - 1,
                    folder,
                    exc,
                    delay,
                )
                if isinstance(exc, (imaplib.IMAP4.abort, socket.timeout, OSError)):
                    self._reconnect()
                    needs_reselect = True
                else:
                    needs_reselect = True
                time.sleep(delay)
                continue
            if status != "OK" or not data:
                if attempt >= UID_BATCH_RETRY_LIMIT:
                    raise ExportError(f"Invalid fetch response for {folder}.")
                delay = UID_BATCH_RETRY_BACKOFF_BASE ** attempt
                needs_reselect = True
                time.sleep(delay)
                continue
            break
        return list(parse_fetch_response(data))

    def _resolve_output_path(self, folder: str) -> Path:
        if self._shared_output_path is None:
            filename = f"mailbox_{self.export_date}.mbox"
            self._shared_output_path = self.out_dir / filename
        return self._shared_output_path

    @staticmethod
    def _encode_folder(folder: str) -> str:
        try:
            folder.encode("ascii")
            return quote_mailbox(folder)
        except UnicodeEncodeError:
            return quote_mailbox(encode_imap_utf7(folder))

    def _log_summary(self) -> None:
        self.logger.info(
            "Export complete: %d folders processed, %d messages written.",
            self.summary_folders,
            self.summary_messages,
        )
        if not self.summary_outputs:
            self.logger.info("No output files generated.")
            return
        self.logger.info("Generated mbox files:")
        for path in self.summary_outputs:
            self.logger.info(" - %s", path)


@dataclass
class FetchedMessage:
    uid: int
    flags: List[str]
    internaldate: Optional[str]
    message: mailbox.mboxMessage


UID_PATTERN = re.compile(r"UID (?P<uid>\d+)")
FLAGS_PATTERN = re.compile(r"FLAGS \((?P<flags>[^)]*)\)")
INTERNALDATE_PATTERN = re.compile(r'INTERNALDATE "?(?P<date>[^"]+)"?')


def parse_fetch_response(
    data: Sequence[object],
) -> Iterable[FetchedMessage]:
    for index in range(0, len(data)):
        item = data[index]
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
        flags = []
        if flags_match and flags_match.group("flags"):
            flags = [flag.strip() for flag in flags_match.group("flags").split()]
        date_match = INTERNALDATE_PATTERN.search(header)
        internaldate = date_match.group("date") if date_match else None
        email_message = message_from_bytes(message_bytes)
        mbox_message = mailbox.mboxMessage(email_message)
        if flags:
            mbox_message["X-IMAP-Flags"] = " ".join(flags)
        if internaldate:
            try:
                when = imaplib.Internaldate2tuple(f'"{internaldate}"'.encode("utf-8"))
                if when:
                    mbox_message.set_from("imap-export", time.mktime(when))
            except Exception:
                pass
            mbox_message["X-IMAP-InternalDate"] = internaldate
        yield FetchedMessage(uid=uid, flags=flags, internaldate=internaldate, message=mbox_message)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logger = configure_logging(args.verbose)

    try:
        since = to_search_date(args.since)
        before = to_search_date(args.before)
        if since and before:
            since_dt = datetime.strptime(since, "%d-%b-%Y")
            before_dt = datetime.strptime(before, "%d-%b-%Y")
            if since_dt >= before_dt:
                raise ExportError("--since must be earlier than --before.")

        user, password = load_credentials(logger)
        sync_state = SyncState.load(args.state)
        exporter = ImapExporter(
            server=DEFAULT_SERVER,
            port=DEFAULT_PORT,
            user=user,
            password=password,
            out_dir=args.out_dir,
            state=sync_state,
            chunk_size=args.chunk_size,
            include_system=args.include_system,
            full=args.full,
            since=since,
            before=before,
            verify_ssl=not args.insecure,
            logger=logger,
        )
        exporter.run()
        return 0
    except ExportError as exc:
        logger.error("Export failed: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.error("Interrupted by user.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
