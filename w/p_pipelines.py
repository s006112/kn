"""
Responsibility:
Run long-lived pipeline worker loops and file scans for the orchestrator.
This module wires the shared `PipelineContext` into torrent intake, X/ytd-dl
URL downloads, pretext processing, extract and premium extract processing,
audio transcription, TTML conversion, and wikilink cleanup.

Used by:
* p.py

Pipelines:
- File scanner: move torrent files, normalize long pretext filenames, enqueue
  existing pretext/extract/premium files when invoked by an orchestrator.
- Periodic scan: sleep between scan-once runs and back off after scan errors.
- X URL download: watch `x.txt`/`X.txt`, classify and clean URLs, download via
  yt-dlp, then remove only the completed URL line.
- Text queues: acquire a file lock, invoke the requested handler method, and
  continue on recoverable queue errors or permanent LLM failures.
- Audio: process the audio queue through the shared GPU audio worker.
- TTML: wait for subtitle file stability, lock each file, convert it, and
  archive the original.
- Wikilinks: periodically clean dead links in the configured sync folder with
  backups enabled.
"""

import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from typing import Any, Callable, Dict, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from p_context import PipelineContext
from p_pretext import PretextHandler, process_pretext_file, request_pretext_processing
from p_extract import ExtractHandler, PremiumExtractHandler
from p_ttml import handle_ttml, is_file_ready
from p_audio import process_audio_queue
from utils_unlink import clean_dead_links
from utils_files import get_next_available_filename, safe_rename
from utils_text import sanitize_and_trim_filename
from helper.helper_llm import LLMPermanentFailure
from helper.helper_ytd import clean_url, classify_url, download


_file_locks: Dict[str, threading.Lock] = {}
_file_locks_mutex = threading.Lock()
TORRENT_SUFFIX = ".torrent"


def acquire_file_lock(file_path: str) -> bool:
    """Acquire a non-blocking in-process lock for `file_path`."""
    with _file_locks_mutex:
        if file_path not in _file_locks:
            _file_locks[file_path] = threading.Lock()
        registered_lock = _file_locks[file_path]
    return registered_lock.acquire(blocking=False)


def release_file_lock(file_path: str) -> None:
    """Release the registered lock for `file_path` when present."""
    with _file_locks_mutex:
        if file_path in _file_locks:
            _file_locks[file_path].release()


def cleanup_file_lock(file_path: str) -> None:
    """Remove the registered lock entry for `file_path` when present."""
    with _file_locks_mutex:
        if file_path in _file_locks:
            del _file_locks[file_path]


@contextmanager
def file_lock(file_path: str):
    """Yield whether a non-blocking lock for `file_path` was acquired."""
    if acquire_file_lock(file_path):
        try:
            yield True
        finally:
            release_file_lock(file_path)
            cleanup_file_lock(file_path)
    else:
        yield False


def get_file_lock_functions() -> Dict[str, Callable[[str], Any]]:
    """Return the file-lock operation mapping used by integration points."""
    return {
        "acquire": acquire_file_lock,
        "release": release_file_lock,
        "cleanup": cleanup_file_lock,
    }


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


def create_pipeline_handlers(
    ctx: PipelineContext,
) -> Tuple[PretextHandler, ExtractHandler, PremiumExtractHandler]:
    pretext_handler = PretextHandler(ctx)
    extract_handler = ExtractHandler(ctx.config, ctx.extract_queue)
    premium_extract_handler = PremiumExtractHandler(
        ctx.config, ctx.premium_extract_queue
    )
    return pretext_handler, extract_handler, premium_extract_handler


def enqueue_if_absent(queue: Queue, path: str) -> None:
    if path not in list(queue.queue):
        queue.put(path)


def process_queue(
    ctx: PipelineContext,
    queue: Queue,
    process: Callable[[str, Callable[..., str]], None],
    method_name: str,
    processed_files: set[str] | None = None,
) -> None:
    intervals = ctx.config.get("INTERVALS", {})
    queue_idle_seconds = intervals.get("TEXT_QUEUE_IDLE_SECONDS", 0.5)
    queue_loop_seconds = intervals.get("TEXT_QUEUE_LOOP_SECONDS", 0.5)
    file_lock_retry_seconds = intervals.get("FILE_LOCK_RETRY_SECONDS", 1)
    while True:
        try:
            if queue.empty():
                time.sleep(queue_idle_seconds)
                continue
            file_path = queue.get()
            with file_lock(file_path) as locked:
                if not locked:
                    queue.put(file_path)
                    queue.task_done()
                    time.sleep(file_lock_retry_seconds)
                    continue
                try:
                    process(file_path, get_next_available_filename)
                    if processed_files and file_path in processed_files:
                        processed_files.discard(file_path)
                except LLMPermanentFailure as e:
                    logging.error(
                        "Resilient Queue: OpenAI API permanent failure for file %s "
                        "(model: %s): %s",
                        e.file_path,
                        e.model,
                        e.reason,
                    )
                except Exception as e:
                    logging.error("%s queue error: %s", method_name, e)
                finally:
                    queue.task_done()
        except Exception as e:
            logging.error("%s queue error (outer): %s", method_name, e)
            queue.task_done()
        time.sleep(queue_loop_seconds)


def process_pretext_queue(ctx: PipelineContext) -> None:
    process_queue(ctx, ctx.pretext_queue, lambda path, _next: process_pretext_file(ctx, path), "process_pretext")


def process_extract_queue(ctx: PipelineContext, handler: ExtractHandler) -> None:
    process_queue(ctx, ctx.extract_queue, handler.process_extract, "process_extract", handler.processed_files)


def process_premium_extract_queue(
    ctx: PipelineContext, handler: PremiumExtractHandler
) -> None:
    process_queue(ctx, ctx.premium_extract_queue, handler.process_premium_extract, "process_premium_extract", handler.processed_files)


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
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines(
            keepends=True
        )
    except FileNotFoundError:
        return False

    for index, line in enumerate(lines):
        if line.strip() == url:
            del lines[index]
            path.write_text("".join(lines), encoding="utf-8", newline="")
            return True
    return False


def process_x_url_download_pipeline(ctx: PipelineContext) -> None:
    current_thread = threading.current_thread()
    current_thread.name = "XUrlDownloadPipeline"
    logged_watch_path = None
    intervals = ctx.config.get("INTERVALS", {})
    download_scan_seconds = intervals.get("DOWNLOAD_SCAN_SECONDS", 30)
    x_resolve_timeout_seconds = intervals.get("X_RESOLVE_TIMEOUT_SECONDS", 20)

    while not ctx.shutdown_flag.is_set():
        wait_seconds = download_scan_seconds
        try:
            target_folder = Path(
                ctx.config.get("DOWNLOAD_TARGET_FOLDER", ctx.config["WHISPER_FOLDER"])
            )
            list_file = Path(
                ctx.config.get("X_URL_LIST_FILE", target_folder / "x.txt")
            )
            active_list_file = resolve_download_url_list_file(list_file)
            target_folder.mkdir(parents=True, exist_ok=True)
            skipped_urls: set[str] = set()

            if active_list_file != logged_watch_path:
                logging.info(
                    "YTD: %s -> %s",
                    active_list_file,
                    target_folder,
                )
                logged_watch_path = active_list_file

            while not ctx.shutdown_flag.is_set():
                list_path = os.fspath(active_list_file)
                with file_lock(list_path) as locked:
                    if not locked:
                        break
                    url, active_list_file = read_next_download_url(
                        active_list_file,
                        skipped_urls,
                    )

                if not url:
                    break

                try:
                    logging.info("XUrlDownloadPipeline: Downloading %s", url)
                    cleaned_url = clean_url(url)
                    output_path, _ = download(
                        url,
                        "720p",
                        output_dir=target_folder,
                        resolve_timeout=x_resolve_timeout_seconds,
                    )
                except Exception as exc:
                    logging.error(
                        "XUrlDownloadPipeline: Download failed for %s: %s",
                        url,
                        exc,
                    )
                    skipped_urls.add(url)
                    continue

                with file_lock(os.fspath(active_list_file)) as locked:
                    removed = (
                        remove_download_url_line(active_list_file, url)
                        if locked
                        else False
                    )

                if removed:
                    logging.info(
                        "XUrlDownloadPipeline: Downloaded %s -> %s",
                        cleaned_url,
                        output_path,
                    )
                else:
                    logging.warning(
                        "XUrlDownloadPipeline: Downloaded %s but URL line was not removed",
                        url,
                    )
                    skipped_urls.add(url)

        except Exception as exc:
            logging.error("XUrlDownloadPipeline: Error during scan: %s", exc)

        if ctx.shutdown_flag.wait(wait_seconds):
            return


def file_scanner(ctx: PipelineContext) -> None:
    """Run one file intake scan: torrent move, pretext normalize/request, extract enqueue, premium enqueue."""
    scan_torrent_watch_folder(ctx.config)

    pretext_watch_folder = os.fspath(ctx.config["PRETEXT_WATCH_FOLDER"])
    extract_watch_folder = os.fspath(ctx.config["EXTRACT_WATCH_FOLDER"])
    premium_watch_folder = os.fspath(ctx.config["PREMIUM_WATCH_FOLDER"])
    pretext_suffix = str(ctx.config["PRETEXT_SUFFIX"]).lower()
    extract_suffixes = tuple(
        str(s).lower() for s in ctx.config["EXTRACT_SUFFIX"] if str(s)
    )

    for filename in os.listdir(pretext_watch_folder):
        filename_lower = filename.lower()
        if not filename_lower.endswith(pretext_suffix):
            continue
        file_path = os.path.join(pretext_watch_folder, filename)
        if len(os.path.splitext(filename)[0]) > 60:
            base_name = os.path.splitext(filename)[0]
            sanitized_base = sanitize_and_trim_filename(base_name)
            new_name = sanitized_base + pretext_suffix
            new_path = os.path.join(pretext_watch_folder, new_name)
            try:
                if not os.path.exists(new_path):
                    safe_rename(file_path, new_path)
                    file_path = new_path
                    logging.debug(
                        "Renamed long filename: %s -> %s", filename, new_name
                    )
            except Exception as e:
                logging.error("Error renaming file: %s", e)
                continue

        if filename_lower.endswith(pretext_suffix) and not any(
            filename_lower.endswith(s) for s in extract_suffixes
        ):
            request_pretext_processing(ctx, file_path)

    for filename in os.listdir(extract_watch_folder):
        filename_lower = filename.lower()
        if any(filename_lower.endswith(s) for s in extract_suffixes):
            file_path = os.path.join(extract_watch_folder, filename)
            enqueue_if_absent(ctx.extract_queue, file_path)

    for filename in os.listdir(premium_watch_folder):
        filename_lower = filename.lower()
        if any(filename_lower.endswith(s) for s in extract_suffixes):
            file_path = os.path.join(premium_watch_folder, filename)
            enqueue_if_absent(ctx.premium_extract_queue, file_path)

    logging.info(
        "Queued: %d pretext, %d extract, %d premium",
        ctx.pretext_queue.qsize(),
        ctx.extract_queue.qsize(),
        ctx.premium_extract_queue.qsize(),
    )


def periodic_file_scanner(ctx: PipelineContext) -> None:
    intervals = ctx.config.get("INTERVALS", {})
    periodic_scan_seconds = intervals.get("PERIODIC_SCAN_SECONDS", 60)
    scan_error_backoff_seconds = intervals.get("SCAN_ERROR_BACKOFF_SECONDS", 60)

    while not ctx.shutdown_flag.is_set():
        try:
            time.sleep(periodic_scan_seconds)
            file_scanner(ctx)
        except Exception as e:
            logging.error("Periodic scanner error: %s", e)
            time.sleep(scan_error_backoff_seconds)


def process_audio_pipeline(ctx: PipelineContext) -> None:
    current_thread = threading.current_thread()
    current_thread.name = "AudioPipeline-GPU"

    process_audio_queue(
        ctx.config,
        ctx.pretext_queue,
        ctx.extract_queue,
        ctx.premium_extract_queue,
        processing_lock=ctx.audio_processing_lock,
        done_folder_path=os.fspath(ctx.config["AUDIO_DONE_FOLDER"]),
    )


def process_ttml_pipeline(ctx: PipelineContext) -> None:
    watch_folder = os.fspath(ctx.config["TTML_WATCH_FOLDER"])
    original_folder = os.fspath(ctx.config["ORIGINAL_FOLDER"])
    intervals = ctx.config.get("INTERVALS", {})
    ttml_scan_seconds = intervals.get("TTML_SCAN_SECONDS", 2)
    file_ready_stability_seconds = intervals.get("FILE_READY_STABILITY_SECONDS", 1.0)
    pipeline_error_backoff_seconds = intervals.get("PIPELINE_ERROR_BACKOFF_SECONDS", 5)

    while not ctx.shutdown_flag.is_set():
        try:
            ttml_files = []
            if os.path.exists(watch_folder):
                ttml_files = [
                    fn
                    for fn in os.listdir(watch_folder)
                    if fn.lower().endswith(".ttml")
                ]

            for fn in ttml_files:
                if ctx.shutdown_flag.is_set():
                    return

                src = os.path.join(watch_folder, fn)
                if not is_file_ready(src, wait=file_ready_stability_seconds):
                    continue

                if acquire_file_lock(src):
                    try:
                        handle_ttml(
                            src,
                            watch_folder,
                            original_folder,
                            sanitize_and_trim_filename,
                            str(ctx.config["PRETEXT_SUFFIX"]),
                        )
                    except Exception as e:
                        logging.error(
                            "TTML Pipeline: Error processing %s: %s", fn, e
                        )
                    finally:
                        release_file_lock(src)
                        cleanup_file_lock(src)

            time.sleep(ttml_scan_seconds)

        except Exception as e:
            logging.error("TTML Pipeline: Error during scan: %s", e)
            time.sleep(pipeline_error_backoff_seconds)


def process_wikilink_cleaning(ctx: PipelineContext) -> None:
    intervals = ctx.config.get("INTERVALS", {})
    wikilink_clean_seconds = intervals.get("WIKILINK_CLEAN_SECONDS", 60)
    while not ctx.shutdown_flag.is_set():
        try:
            clean_dead_links(
                target_dir=os.fspath(ctx.config["OBSIDIAN_SYNC_FOLDER"]),
                backup_dir=os.fspath(ctx.config["LINK_BACKUP_FOLDER"]),
                create_backup=True,
                dry_run=False,
                max_files=50,
                file_lock_functions=get_file_lock_functions(),
            )

        except Exception:
            pass  # Suppress errors for now, as logging is removed

        if ctx.shutdown_flag.wait(wikilink_clean_seconds):
            return
