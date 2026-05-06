"""
Responsibility:
Detect `.torrent` files in the configured watch folder during scan flows and move
them into the configured Whisper folder.

Used by:
* w/p_pipelines.py

Pipelines:
- watch folder scan -> torrent detection -> file lock -> safe move -> w folder

Invariants:
- Only `.torrent` files are handled by this module.
- Moved files keep the `.torrent` extension.
- Destination paths never overwrite an existing file.

Out of scope:
- Torrent parsing, downloading, metadata extraction, or client integration.
- Watchdog event handling outside the existing scan-driven pipeline flow.
"""

import logging
import os
from typing import Any, Dict

from utils_files import safe_rename
from utils_lock_registry import (
    acquire_file_lock,
    cleanup_file_lock,
    release_file_lock,
)


TORRENT_SUFFIX = ".torrent"


def _next_available_torrent_path(destination_folder: str, filename: str) -> str:
    """
    Purpose:
    Build a destination path that preserves the torrent filename without overwriting
    an existing file.
    Inputs:
    - destination_folder: Folder where the torrent should be moved.
    - filename: Source filename, including extension.
    Outputs:
    - Full destination path that does not currently exist.
    Side effects:
    - Reads destination path existence from disk.
    Failure modes:
    - May loop until a non-existing filename is found.
    """
    candidate = os.path.join(destination_folder, filename)
    if not os.path.exists(candidate):
        return candidate

    base_name, ext = os.path.splitext(filename)
    counter = 1
    while True:
        candidate = os.path.join(
            destination_folder, f"{base_name}_{counter}{ext}"
        )
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def move_torrent_to_whisper(file_path: str, whisper_folder: str) -> bool:
    """
    Purpose:
    Move one torrent file into the Whisper folder.
    Inputs:
    - file_path: Full source path to a `.torrent` file.
    - whisper_folder: Destination folder for torrent files.
    Outputs:
    - True when the file was moved, otherwise False.
    Side effects:
    - Creates the destination folder if missing.
    - Renames the file on disk.
    Failure modes:
    - Returns False when the path is missing, not a torrent file, or locked.
    - Logs and returns False when the move cannot be completed.
    """
    normalized_path = os.path.abspath(os.fspath(file_path))
    destination_folder = os.path.abspath(os.fspath(whisper_folder))

    if not normalized_path.lower().endswith(TORRENT_SUFFIX):
        return False
    if not os.path.isfile(normalized_path):
        return False
    if not acquire_file_lock(normalized_path):
        return False

    try:
        os.makedirs(destination_folder, exist_ok=True)
        destination_path = _next_available_torrent_path(
            destination_folder,
            os.path.basename(normalized_path),
        )
        moved_path = safe_rename(normalized_path, destination_path)
        if os.path.abspath(moved_path) != os.path.abspath(destination_path):
            logging.warning("Torrent: Failed to move %s", normalized_path)
            return False

        logging.info("Torrent: Moved %s", os.path.basename(destination_path))
        return True
    finally:
        release_file_lock(normalized_path)
        cleanup_file_lock(normalized_path)


def scan_torrent_watch_folder(config: Dict[str, Any]) -> int:
    """
    Purpose:
    Scan the configured watch folder and move any `.torrent` files into the
    configured Whisper folder.
    Inputs:
    - config: Pipeline configuration containing `WATCH_FOLDER` and `WHISPER_FOLDER`.
    Outputs:
    - Count of torrent files moved during this scan.
    Side effects:
    - Reads the watch folder and may move matching files.
    Failure modes:
    - Returns 0 when the watch folder is missing; propagates unexpected filesystem
      errors from directory listing.
    """
    watch_folder = os.path.abspath(os.fspath(config["WATCH_FOLDER"]))
    whisper_folder = os.path.abspath(os.fspath(config["WHISPER_FOLDER"]))

    if not os.path.exists(watch_folder):
        return 0

    moved_count = 0
    for filename in os.listdir(watch_folder):
        if not filename.lower().endswith(TORRENT_SUFFIX):
            continue
        file_path = os.path.join(watch_folder, filename)
        if move_torrent_to_whisper(file_path, whisper_folder):
            moved_count += 1

    return moved_count
