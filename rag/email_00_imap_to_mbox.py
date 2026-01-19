#!/usr/bin/env python3
"""
Responsibility:
Export messages from one or more IMAP folders into a local mbox archive, with an
optional incremental mode that persists per-folder progress (last exported UID)
to a JSON state file.

Used by:
* (no direct callers found)

Pipelines:
- argv -> parse_args -> load_credentials -> connect -> list_folders -> search -> fetch -> mbox_write -> state_save

Invariants:
- Incremental mode only exports UIDs greater than the stored last UID per folder.
- The state file stores a mapping of folder name to last exported UID (int).
- The exporter writes messages into a single mbox file for the current run date.
- IMAP flags and internal date are preserved in custom headers when present.

Out of scope:
- Attachment extraction and indexing.
- Message de-duplication across folders or runs.
- Concurrent exports or resumable partial writes within a single run.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from email import message_from_bytes
import mailbox
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from dotenv import load_dotenv
import imaplib

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.utils_imap_client import ImapClient, RawFetchedRecord
# Keep defaults stable for consistent CLI behavior across runs.

DEFAULT_SERVER = "mail.ampco.com.hk"
DEFAULT_PORT = 993
DEFAULT_TIMEOUT = 300
DEFAULT_SINCE_DATE = "2026-01-17"
DEFAULT_OUT_DIR = Path("data/mbox/raw")
DEFAULT_STATE_PATH = Path("data/mbox/.state/imap_state.json")
DEFAULT_CHUNK_SIZE = 100


class ExportError(RuntimeError):
    """
    Purpose:
    Signal a recoverable, user-facing export failure.

    Inputs:
    - Message passed to the RuntimeError constructor.

    Outputs:
    - An exception instance to be raised.

    Side effects:
    - None.

    Failure modes:
    - None.
    """


# ---------------------------------------------------------------------
# CLI / logging / credential
# ---------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """
    Purpose:
    Parse CLI flags for the IMAP export run.

    Inputs:
    - argv: Optional argument vector (without program name). Uses sys.argv when None.

    Outputs:
    - argparse.Namespace containing parsed options.

    Side effects:
    - Reads command-line arguments via argparse.

    Failure modes:
    - argparse may raise SystemExit on invalid arguments.
    """
    parser = argparse.ArgumentParser(
        description="Export IMAP folders into local mbox files."
    )
    parser.add_argument("--since", type=str, default=DEFAULT_SINCE_DATE)
    parser.add_argument("--before", type=str)
    parser.add_argument("--full", action="store_true")
    parser.add_argument(
        "--out-dir", type=Path, default=DEFAULT_OUT_DIR
    )
    parser.add_argument(
        "--state", type=Path, default=DEFAULT_STATE_PATH
    )
    parser.add_argument(
        "--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE
    )
    parser.add_argument(
        "--include-system", action="store_true"
    )
    parser.add_argument(
        "--verbose", action="store_true"
    )
    parser.add_argument(
        "--insecure", action="store_true",
        help="Disable SSL verification (use only for testing).",
    )
    return parser.parse_args(argv)


def configure_logging(verbose: bool) -> logging.Logger:
    """
    Purpose:
    Configure root logging and return a named logger for this script.

    Inputs:
    - verbose: If True, configure DEBUG logging; otherwise INFO.

    Outputs:
    - logging.Logger for the name "imap_export".

    Side effects:
    - Calls logging.basicConfig which configures global logging handlers.

    Failure modes:
    - None.
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("imap_export")


def load_credentials(logger: logging.Logger) -> Tuple[str, str]:
    """
    Purpose:
    Load IMAP username and password from environment variables.

    Inputs:
    - logger: Logger used for user-facing error messages.

    Outputs:
    - Tuple of (user, password).

    Side effects:
    - Loads variables from a .env file via dotenv (if present).
    - Reads process environment variables.

    Failure modes:
    - Raises ExportError when IMAP_USER or IMAP_PASSWORD is missing.
    """
    load_dotenv()
    user = os.getenv("IMAP_USERNAME")
    password = os.getenv("IMAP_PASSWORD")
    if not user or not password:
        logger.error("Missing IMAP_USER or IMAP_PASSWORD in environment.")
        raise ExportError("Missing IMAP credentials.")
    return user, password


# ---------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------

def to_search_date(value: Optional[str]) -> Optional[str]:
    """
    Purpose:
    Convert a YYYY-MM-DD date string into an IMAP search date (DD-Mon-YYYY).

    Inputs:
    - value: Date string in YYYY-MM-DD, or None/empty to disable the constraint.

    Outputs:
    - IMAP-formatted date string, or None if input is falsy.

    Side effects:
    - None.

    Failure modes:
    - Raises ExportError when the input date format is invalid.
    """
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
    """
    Purpose:
    Build an IMAP SEARCH criteria list for the target date constraints.

    Inputs:
    - since: IMAP date string (DD-Mon-YYYY) for SINCE, or None.
    - before: IMAP date string (DD-Mon-YYYY) for BEFORE, or None.

    Outputs:
    - List of IMAP SEARCH tokens suitable for client.search_uids(...).

    Side effects:
    - None.

    Failure modes:
    - None.
    """
    criteria: List[str] = ["ALL"]
    if since:
        criteria.append(f"SINCE {since}")
    if before:
        criteria.append(f"BEFORE {before}")
    return criteria


def ensure_parent(path: Path) -> None:
    """
    Purpose:
    Ensure the parent directory of a path exists.

    Inputs:
    - path: Target file path whose parent directory should be created.

    Outputs:
    - None.

    Side effects:
    - Creates directories on the filesystem.

    Failure modes:
    - May raise OSError if the directory cannot be created.
    """
    path.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------
# State
# ---------------------------------------------------------------------

@dataclass
class SyncState:
    """
    Purpose:
    Persist and retrieve per-folder incremental export progress.

    Inputs:
    - path: Filesystem location of the JSON state file.
    - data: Mapping of folder name to last exported UID (int).

    Outputs:
    - A SyncState instance.

    Side effects:
    - None.

    Failure modes:
    - None.
    """
    path: Path
    data: Dict[str, int]

    @classmethod
    def load(cls, path: Path) -> "SyncState":
        """
        Purpose:
        Load state from disk and sanitize it into a folder->uid mapping.

        Inputs:
        - path: JSON state file path.

        Outputs:
        - SyncState instance with sanitized content (may be empty).

        Side effects:
        - Reads from the filesystem.

        Failure modes:
        - JSON/OSError/ValueError are swallowed and result in an empty state.
        """
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
        """
        Purpose:
        Look up the last exported UID for a folder.

        Inputs:
        - folder: IMAP folder name.

        Outputs:
        - Last exported UID, or None if the folder is not present.

        Side effects:
        - None.

        Failure modes:
        - None.
        """
        return self.data.get(folder)

    def update(self, folder: str, uid: int) -> None:
        """
        Purpose:
        Record a new last exported UID for a folder.

        Inputs:
        - folder: IMAP folder name.
        - uid: Last exported UID to persist.

        Outputs:
        - None.

        Side effects:
        - Mutates in-memory state.

        Failure modes:
        - None.
        """
        self.data[folder] = uid

    def save(self) -> None:
        """
        Purpose:
        Persist the current state to disk as JSON.

        Inputs:
        - None.

        Outputs:
        - None.

        Side effects:
        - Creates parent directories and writes a JSON file.

        Failure modes:
        - May raise OSError if the file cannot be written.
        """
        ensure_parent(self.path)
        payload = {"folders": self.data}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------
# mbox 寫入
# ---------------------------------------------------------------------

def records_to_mbox_messages(records: Iterable[RawFetchedRecord]) -> Iterable[mailbox.mboxMessage]:
    """
    Purpose:
    Convert fetched IMAP records into mboxMessage objects with metadata headers.

    Inputs:
    - records: Iterable of RawFetchedRecord containing raw RFC822 bytes and metadata.

    Outputs:
    - Iterable of (uid, mailbox.mboxMessage) pairs via generator yields.

    Side effects:
    - None.

    Failure modes:
    - Parsing/conversion errors may propagate except for Internaldate conversion,
      which is best-effort and swallowed.
    """
    for record in records:
        email_message = message_from_bytes(record.raw_bytes)
        mbox_message = mailbox.mboxMessage(email_message)
        if record.flags:
            mbox_message["X-IMAP-Flags"] = " ".join(record.flags)
        if record.internaldate:
            try:
                when = imaplib.Internaldate2tuple(
                    f'"{record.internaldate}"'.encode("utf-8")
                )
                if when:
                    mbox_message.set_from("imap-export", time.mktime(when))
            except Exception:
                pass
            mbox_message["X-IMAP-InternalDate"] = record.internaldate
        yield record.uid, mbox_message


# ---------------------------------------------------------------------
# Export 主流程
# ---------------------------------------------------------------------

def export_all(
    client: ImapClient,
    state: SyncState,
    out_dir: Path,
    chunk_size: int,
    full: bool,
    since: Optional[str],
    before: Optional[str],
    include_system: bool,
    logger: logging.Logger,
) -> Tuple[int, List[Path]]:
    """
    Purpose:
    Export all discovered folders into a single dated mbox file.

    Inputs:
    - client: Connected-capable IMAP client.
    - state: Incremental state store (updated in-memory during export).
    - out_dir: Output directory for the mbox archive file.
    - chunk_size: Maximum number of UIDs fetched per batch.
    - full: If True, ignore state filtering and overwrite the day's mbox file.
    - since: Optional IMAP date string for SINCE constraint.
    - before: Optional IMAP date string for BEFORE constraint.
    - include_system: If True, include system folders when listing folders.
    - logger: Logger used for progress and warnings.

    Outputs:
    - (total_written, exported_files) where exported_files contains the mbox path if created.

    Side effects:
    - Opens a network connection to the IMAP server.
    - Creates/overwrites an mbox file and writes messages to disk.
    - Updates SyncState in memory.

    Failure modes:
    - IMAP errors and filesystem errors may propagate as exceptions.
    """
    client.connect()
    try:
        folders = client.list_folders(include_system=include_system)
        if not folders:
            logger.warning("No folders discovered.")
            return 0, []

        logger.info("Discovered %d folders to process.", len(folders))

        export_date = datetime.now().strftime("%Y%m%d")
        output_path = out_dir / f"mailbox_{export_date}.mbox"
        ensure_parent(output_path)
        if full and output_path.exists():
            output_path.unlink()

        total_written = 0
        exported_files: List[Path] = []

        mbox = mailbox.mbox(output_path, create=True)
        try:
            mbox.lock()
            for folder in folders:
                n = export_folder(
                    client=client,
                    folder=folder,
                    state=state,
                    mbox=mbox,
                    chunk_size=chunk_size,
                    full=full,
                    since=since,
                    before=before,
                    logger=logger,
                )
                total_written += n
            mbox.flush()
            exported_files.append(output_path.resolve())
        finally:
            try:
                mbox.unlock()
            except Exception:
                pass
            mbox.close()

        return total_written, exported_files

    finally:
        client.disconnect()


def export_folder(
    client: ImapClient,
    folder: str,
    state: SyncState,
    mbox: mailbox.mbox,
    chunk_size: int,
    full: bool,
    since: Optional[str],
    before: Optional[str],
    logger: logging.Logger,
) -> int:
    """
    Purpose:
    Export messages from a single IMAP folder into an open mbox archive.

    Inputs:
    - client: IMAP client used to search and fetch.
    - folder: Folder name to export.
    - state: Incremental state store (read for filtering and updated on success).
    - mbox: Open mailbox.mbox instance to append messages to.
    - chunk_size: Maximum number of UIDs fetched per batch.
    - full: If True, do not filter by stored last UID.
    - since: Optional IMAP date string for SINCE constraint.
    - before: Optional IMAP date string for BEFORE constraint.
    - logger: Logger used for progress messages.

    Outputs:
    - Number of messages written for this folder.

    Side effects:
    - Issues IMAP SEARCH/FETCH operations over the network.
    - Appends messages to the provided mbox file.
    - Updates SyncState in memory when messages are written.

    Failure modes:
    - IMAP errors and mailbox write errors may propagate as exceptions.
    """
    criteria = build_search_criteria(since, before)
    uids = client.search_uids(folder, criteria)
    if not uids:
        logger.info("Folder %s: no messages match criteria.", folder)
        return 0

    if not full:
        last_uid = state.last_uid(folder)
        if last_uid:
            uids = [uid for uid in uids if uid > last_uid]
    if not uids:
        logger.info("Folder %s: no new messages after filtering.", folder)
        return 0

    logger.info(
        "Folder %s: %d messages to export (chunk size %d).",
        folder,
        len(uids),
        chunk_size,
    )

    written = 0
    max_uid = 0

    for start in range(0, len(uids), chunk_size):
        batch = uids[start : start + chunk_size]
        raw_records = client.fetch_batch(folder, batch)
        for uid, mbox_msg in records_to_mbox_messages(raw_records):
            mbox.add(mbox_msg)
            written += 1
            if uid > max_uid:
                max_uid = uid

    if written:
        state.update(folder, max_uid)
        logger.info(
            "Folder %s: written %d messages.",
            folder,
            written,
        )
    return written


# ---------------------------------------------------------------------
# main
# ---------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    Purpose:
    Entry point for CLI execution.

    Inputs:
    - argv: Optional argument vector (without program name). Uses sys.argv when None.

    Outputs:
    - Process exit code (0 on success, 1 on failure).

    Side effects:
    - Configures logging, reads environment and state file, performs IMAP export,
      and writes output files to disk.

    Failure modes:
    - Returns 1 for ExportError and KeyboardInterrupt; other exceptions may propagate.
    """
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
        state = SyncState.load(args.state)

        client = ImapClient(
            server=DEFAULT_SERVER,
            port=DEFAULT_PORT,
            user=user,
            password=password,
            verify_ssl=not args.insecure,
            timeout=DEFAULT_TIMEOUT,
            logger=logger,
        )

        total, files = export_all(
            client=client,
            state=state,
            out_dir=args.out_dir,
            chunk_size=args.chunk_size,
            full=args.full,
            since=since,
            before=before,
            include_system=args.include_system,
            logger=logger,
        )

        state.save()
        logger.info(
            "Export complete: %d messages written.",
            total,
        )
        if files:
            logger.info("Generated mbox files:")
            for p in files:
                logger.info(" - %s", p)

        return 0

    except ExportError as exc:
        logger.error("Export failed: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.error("Interrupted by user.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
