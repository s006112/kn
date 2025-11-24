#!/usr/bin/env python3
"""
Standalone service for cleaning Obsidian wikilinks.

This tool is intentionally isolated from the p.py pipeline and
reuses the shared implementation in utils_unlink.py.
"""

import logging
import os
import sys
import time
from pathlib import Path

from utils_unlink import clean_dead_links, setup_wikilink_cleaner_logging

# -------------------------
# Wikilink cleaner config
# -------------------------
WIKILINK_CLEAN_INTERVAL_MINUTES = 2
WIKILINK_CLEAN_TARGET_DIR = Path("/desktop/Obsidian/O_2025")
WIKILINK_CLEAN_BACKUP_DIR: Path | None = None
WIKILINK_CLEAN_MAX_FILES = 50
WIKILINK_CLEAN_DRY_RUN = False
WIKILINK_CLEAN_CREATE_BACKUP = True


def build_logger() -> logging.Logger:
    logger = logging.getLogger("wikilink_cleaner_cli")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def clean_cycle(logger: logging.Logger) -> None:
    stats = clean_dead_links(
        target_dir=os.fspath(WIKILINK_CLEAN_TARGET_DIR),
        backup_dir=os.fspath(WIKILINK_CLEAN_BACKUP_DIR)
        if WIKILINK_CLEAN_BACKUP_DIR
        else None,
        create_backup=bool(WIKILINK_CLEAN_CREATE_BACKUP),
        dry_run=bool(WIKILINK_CLEAN_DRY_RUN),
        max_files=int(WIKILINK_CLEAN_MAX_FILES),
        file_lock_functions=None,
    )

    logger.info(
        "Completed wikilink cleaning - Files: %d, Links removed: %d, Files modified: %d, Errors: %d",
        stats.get("files_processed", 0),
        stats.get("broken_links_removed", 0),
        stats.get("files_modified", 0),
        stats.get("errors", 0),
    )


def main() -> int:
    logger = build_logger()
    setup_wikilink_cleaner_logging(logger)
    interval_seconds = max(1, int(WIKILINK_CLEAN_INTERVAL_MINUTES) * 60)
    logger.info(
        "Starting continuous wikilink cleaner (interval: %d minutes, target: %s)",
        int(WIKILINK_CLEAN_INTERVAL_MINUTES),
        WIKILINK_CLEAN_TARGET_DIR,
    )

    try:
        while True:
            start_time = time.monotonic()
            clean_cycle(logger)
            elapsed = time.monotonic() - start_time
            sleep_time = max(0, interval_seconds - elapsed)
            if sleep_time:
                logger.info(
                    "Next wikilink cleaning cycle in %.1f seconds", sleep_time
                )
                time.sleep(sleep_time)
    except KeyboardInterrupt:
        logger.info("Wikilink cleaner stopped by user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
