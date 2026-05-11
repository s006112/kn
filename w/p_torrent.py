"""
p_torrent.py

Responsibility:
Scan the watch folder for torrent files and move them into the Whisper folder.
"""

import logging
import os
import threading
from typing import Any, Dict

from .helper_files import safe_rename

TORRENT_SUFFIX = ".torrent"
_torrent_locks = {}
_torrent_locks_mutex = threading.Lock()


def _next_available_torrent_path(destination_folder: str, filename: str) -> str:
    """Return a non-existing torrent destination path for `filename`."""
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
    """Move one `.torrent` file into the Whisper folder."""
    normalized_path = os.path.abspath(os.fspath(file_path))
    destination_folder = os.path.abspath(os.fspath(whisper_folder))

    if not normalized_path.lower().endswith(TORRENT_SUFFIX):
        return False
    if not os.path.isfile(normalized_path):
        return False

    with _torrent_locks_mutex:
        lock = _torrent_locks.setdefault(normalized_path, threading.Lock())
    if not lock.acquire(blocking=False):
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
        with _torrent_locks_mutex:
            _torrent_locks.pop(normalized_path, None)
        lock.release()


def scan_torrent_watch_folder(config: Dict[str, Any]) -> int:
    """Scan the watch folder and move `.torrent` files into the Whisper folder."""
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


def process_torrent_pipeline(config, shutdown_flag) -> None:
    current_thread = threading.current_thread()
    current_thread.name = "TorrentPipeline"
    scan_seconds = config["INTERVALS"]["SCAN_SECONDS"]

    scan_torrent_watch_folder(config)

    while not shutdown_flag.is_set():
        if shutdown_flag.wait(scan_seconds):
            return
        scan_torrent_watch_folder(config)
