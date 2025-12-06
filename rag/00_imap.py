#!/usr/bin/env python3
"""Export mailboxes over IMAP into local mbox archives with incremental sync support."""

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
# 與原程式相同的常數 ----------------------------------------------

DEFAULT_SERVER = "mail.ampco.com.hk"
DEFAULT_PORT = 993
DEFAULT_TIMEOUT = 300
DEFAULT_SINCE_DATE = "2025-11-30"
DEFAULT_OUT_DIR = Path("data") / "raw" / "mbox"
DEFAULT_STATE_PATH = Path(".state") / "imap_state.json"
DEFAULT_CHUNK_SIZE = 100


class ExportError(RuntimeError):
    """Raised when the export process fails."""


# ---------------------------------------------------------------------
# CLI / logging / credential
# ---------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
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
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("imap_export")


def load_credentials(logger: logging.Logger) -> Tuple[str, str]:
    load_dotenv()
    user = os.getenv("IMAP_USER")
    password = os.getenv("IMAP_PASSWORD")
    if not user or not password:
        logger.error("Missing IMAP_USER or IMAP_PASSWORD in environment.")
        raise ExportError("Missing IMAP credentials.")
    return user, password


# ---------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------

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


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------
# State
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# mbox 寫入
# ---------------------------------------------------------------------

def records_to_mbox_messages(records: Iterable[RawFetchedRecord]) -> Iterable[mailbox.mboxMessage]:
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
