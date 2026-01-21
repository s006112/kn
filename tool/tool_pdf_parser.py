#!/usr/bin/env python3
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_pdf import get_pdf_full_text  # noqa: E402


WATCH_FOLDER = Path("/desktop/Sync/Whisper")
TARGET_DONE_FOLDER = WATCH_FOLDER

logger = logging.getLogger(__name__)


def _is_pdf(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"


def _write_pdf_txt(pdf_path: Path) -> None:
    txt_path = pdf_path.with_suffix(".txt")
    raw_text = get_pdf_full_text(pdf_path.read_bytes(), filename=pdf_path.name)
    txt_path.write_text(raw_text, encoding="utf-8")


def _handle_pdf(path: Path) -> None:
    time.sleep(0.5)
    try:
        if _is_pdf(path):
            _write_pdf_txt(path)
            logger.info("Wrote TXT: %s", path.with_suffix(".txt").name)
    except Exception:
        logger.exception("Error processing %s", path.name)


def process_existing_pdfs() -> None:
    pdfs = sorted(p for p in WATCH_FOLDER.glob("*.pdf") if _is_pdf(p))
    for pdf_path in pdfs:
        _handle_pdf(pdf_path)


class PdfCreatedHandler(FileSystemEventHandler):
    def on_created(self, event):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if _is_pdf(path):
            logger.info("Detected new file: %s", path.name)
            _handle_pdf(path)

    def on_moved(self, event):
        if event.is_directory:
            return
        path = Path(getattr(event, "dest_path", "") or "")
        if _is_pdf(path):
            logger.info("Detected moved file: %s", path.name)
            _handle_pdf(path)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if not WATCH_FOLDER.exists():
        logger.error("Watch folder not found: %s", WATCH_FOLDER)
        return 1

    process_existing_pdfs()

    observer = Observer()
    observer.schedule(PdfCreatedHandler(), str(WATCH_FOLDER), recursive=False)
    observer.start()
    logger.info("Watching: %s", WATCH_FOLDER)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
