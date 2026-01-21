#!/usr/bin/env python3
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helper.helper_pdf import get_pdf_full_text  # noqa: E402


WATCH_FOLDER = Path("/desktop/Sync/Whisper")
TARGET_DONE_FOLDER = WATCH_FOLDER

logger = logging.getLogger(__name__)


def _get_target_owner() -> tuple[int, int] | None:
    for uid_key, gid_key in (
        ("SUDO_UID", "SUDO_GID"),
        ("HOST_UID", "HOST_GID"),
        ("LOCAL_UID", "LOCAL_GID"),
    ):
        uid = os.environ.get(uid_key)
        gid = os.environ.get(gid_key)
        if uid and gid and uid.isdigit() and gid.isdigit():
            return int(uid), int(gid)
    return None


def _make_editable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        os.chmod(path, mode | 0o666)
    except Exception:
        pass

    owner = _get_target_owner()
    if owner and hasattr(os, "chown") and os.geteuid() == 0:
        try:
            os.chown(path, owner[0], owner[1])
        except Exception:
            pass


def _is_pdf(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"


def _write_pdf_txt(pdf_path: Path) -> None:
    txt_path = pdf_path.with_suffix(".txt")
    raw_text = get_pdf_full_text(pdf_path.read_bytes(), filename=pdf_path.name)
    txt_path.write_text(raw_text, encoding="utf-8")
    _make_editable(txt_path)


def _trash_file(path: Path) -> None:
    try:
        from send2trash import send2trash  # type: ignore
    except Exception:
        send2trash = None

    if send2trash is not None:
        send2trash(str(path))
        return

    try:
        xdg_data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        trash_root = xdg_data_home / "Trash"
        files_dir = trash_root / "files"
        info_dir = trash_root / "info"
        files_dir.mkdir(parents=True, exist_ok=True)
        info_dir.mkdir(parents=True, exist_ok=True)

        base_name = path.name
        dest = files_dir / base_name
        if dest.exists():
            stem, suffix = path.stem, path.suffix
            for i in range(1, 10_000):
                cand = files_dir / f"{stem}.{i}{suffix}"
                if not cand.exists():
                    dest = cand
                    break

        shutil.move(str(path), str(dest))

        info_path = info_dir / f"{dest.name}.trashinfo"
        deletion_date = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        info = f"[Trash Info]\nPath={quote(str(path))}\nDeletionDate={deletion_date}\n"
        info_path.write_text(info, encoding="utf-8")
        _make_editable(info_path)
        return
    except Exception:
        pass

    gio = shutil.which("gio")
    if gio:
        subprocess.run([gio, "trash", str(path)], check=True)
        return

    raise RuntimeError("No trash implementation available (install send2trash).")


def _handle_pdf(path: Path) -> None:
    time.sleep(0.5)
    try:
        if _is_pdf(path):
            _write_pdf_txt(path)
            logger.info("Wrote TXT: %s", path.with_suffix(".txt").name)
            _trash_file(path)
            logger.info("Trashed PDF: %s", path.name)
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
