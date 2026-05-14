# p_ytd.py
from __future__ import annotations

import logging
from .helper_text import short_log_name
import threading
from contextlib import contextmanager
from pathlib import Path
from helper.helper_ytd import clean_url, classify_url, download
from .helper_files import release_text_file_permissions, write_text_file


_ytd_list_lock = threading.Lock()


@contextmanager
def list_file_lock():
    locked = _ytd_list_lock.acquire(blocking=False)
    try:
        yield locked
    finally:
        if locked:
            _ytd_list_lock.release()


def resolve_download_url_list_file(list_file):
    path = Path(list_file)
    if path.exists() or path.name != "x.txt":
        return path
    alt = path.with_name("X.txt")
    return alt if alt.exists() else path


def read_next_download_url(list_file, skipped_urls):
    path = resolve_download_url_list_file(list_file)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                url = line.strip()
                if url and url not in skipped_urls and classify_url(url):
                    return url, path
    except FileNotFoundError:
        pass
    return None, path


def remove_download_url_line(list_file, url):
    path = resolve_download_url_list_file(list_file)
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except FileNotFoundError:
        return False

    for index, line in enumerate(lines):
        if line.strip() == url:
            del lines[index]
            write_text_file(path, "".join(lines), newline="")
            return True
    return False


def download_ytd_url(url, target_folder, resolve_timeout):
    logging.info("YTDPipeline: Downloading %s", short_log_name(url))
    cleaned_url = clean_url(url)
    output_path, _ = download(url, "720p", output_dir=target_folder, resolve_timeout=resolve_timeout)
    release_text_file_permissions(output_path)
    return cleaned_url, output_path


def process_ytd_pipeline(config, shutdown_flag) -> None:
    threading.current_thread().name = "YTDPipeline"
    intervals = config.get("INTERVALS", {})
    scan_seconds = intervals.get("SCAN_SECONDS", 60)
    ytd_resolve_timeout_seconds = intervals.get("YTD_RESOLVE_TIMEOUT_SECONDS", 20)

    while not shutdown_flag.is_set():
        try:
            target_folder = Path(
                config.get("DOWNLOAD_TARGET_FOLDER", config["WHISPER_FOLDER"])
            )
            list_file = Path(config["YTD_LIST_FILE"])
            active_list_file = resolve_download_url_list_file(list_file)
            target_folder.mkdir(parents=True, exist_ok=True)
            skipped_urls: set[str] = set()

            while not shutdown_flag.is_set():
                with list_file_lock() as locked:
                    if not locked:
                        break
                    url, active_list_file = read_next_download_url(
                        active_list_file,
                        skipped_urls,
                    )

                if not url:
                    break

                try:
                    cleaned_url, output_path = download_ytd_url(url, target_folder, ytd_resolve_timeout_seconds)
                except Exception as exc:
                    logging.error("YTDPipeline: Download failed for %s: %s", short_log_name(url), exc)
                    skipped_urls.add(url)
                    continue

                with list_file_lock() as locked:
                    removed = remove_download_url_line(active_list_file, url) if locked else False

                if removed:
                    logging.info("YTDPipeline: Downloaded %s -> %s", short_log_name(cleaned_url), short_log_name(output_path))
                else:
                    logging.warning(
                        "YTDPipeline: Downloaded %s but URL line was not removed",
                        short_log_name(url),
                    )
                    skipped_urls.add(url)

        except Exception as exc:
            logging.error("YTDPipeline: Error during scan: %s", exc)

        if shutdown_flag.wait(scan_seconds):
            return
