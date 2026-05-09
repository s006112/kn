""" p_pipelines.py -
Runtime pipeline orchestration.

Used by:
- p.py
- p_h.py

Flows:
- scanner -> torrent / audio / ttml / pretext / extract intake
- text queue -> file lock -> processor -> archive / fail
- audio queue -> gpu worker -> archive
- ttml queue -> ready check -> convert -> archive
- ytd worker -> read X.txt -> download -> remove completed URL
- wikilink worker -> clean dead links -> backup
"""

import logging
import os
import threading
import time
from contextlib import contextmanager
from queue import Empty, Queue
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Set

from .p_pretext import process_pretext_file, request_pretext_processing
from .p_extract import ExtractProcessor, PremiumExtractProcessor
from .p_ttml import handle_ttml, is_file_ready
from .p_audio import process_audio_queue, scan_audio_files
from .p_ytd import process_ytd_pipeline
from .utils_unlink import clean_dead_links
from .helper_files import get_next_available_filename, safe_rename
from .helper_text import sanitize_and_trim_filename
from helper.helper_llm import LLMPermanentFailure

_file_locks: Dict[str, threading.Lock] = {}
_file_locks_mutex = threading.Lock()
TORRENT_SUFFIX = ".torrent"

@dataclass
class PipelineContext:
    config: Dict[str, Any]
    pretext_queue: Queue = field(default_factory=Queue)
    extract_queue: Queue = field(default_factory=Queue)
    premium_extract_queue: Queue = field(default_factory=Queue)
    audio_queue: Queue = field(default_factory=Queue)
    ttml_queue: Queue = field(default_factory=Queue)
    text_processing_lock: threading.Lock = field(default_factory=threading.Lock)
    audio_processing_lock: threading.Lock = field(default_factory=threading.Lock)
    processed_files_global: Set[str] = field(default_factory=set)
    processed_files_lock: threading.Lock = field(default_factory=threading.Lock)
    wikilink_cleaning_stats: Dict[str, Any] = field(
        default_factory=lambda: {"last_run": None, "cycle_count": 0}
    )
    shutdown_flag: threading.Event = field(default_factory=threading.Event)

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


def create_extract_processors(ctx: PipelineContext):
    extract_processor = ExtractProcessor(ctx.config)
    premium_extract_processor = PremiumExtractProcessor(ctx.config)
    return extract_processor, premium_extract_processor

def enqueue_if_absent(queue: Queue, path: str) -> None:
    if path not in list(queue.queue):
        queue.put(path)


def process_queue(
    ctx: PipelineContext,
    queue: Queue,
    process: Callable[[str, Callable[..., str]], None],
    method_name: str,
) -> None:
    intervals = ctx.config.get("INTERVALS", {})
    wait_seconds = intervals.get("WAIT_SECONDS", 1.0)
    while True:
        file_path = None
        try:
            if queue.empty():
                time.sleep(wait_seconds)
                continue
            file_path = queue.get()
            with file_lock(file_path) as locked:
                if not locked:
                    queue.put(file_path)
                    queue.task_done()
                    file_path = None
                    time.sleep(wait_seconds)
                    continue
                try:
                    process(file_path, get_next_available_filename)
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
                    file_path = None
        except Exception as e:
            logging.error("%s queue error (outer): %s", method_name, e)
            if file_path is not None:
                queue.task_done()
        time.sleep(wait_seconds)


def process_pretext_queue(ctx: PipelineContext) -> None:
    process_queue(ctx, ctx.pretext_queue, lambda path, _next: process_pretext_file(ctx.config, path, ctx.processed_files_global, ctx.processed_files_lock), "process_pretext")


def process_extract_queue(ctx: PipelineContext, processor: ExtractProcessor) -> None:
    process_queue(ctx, ctx.extract_queue, processor.process_extract, "process_extract")


def process_premium_extract_queue(
    ctx: PipelineContext, processor: PremiumExtractProcessor
) -> None:
    process_queue(ctx, ctx.premium_extract_queue, processor.process_premium_extract, "process_premium_extract")


def file_scanner(ctx: PipelineContext) -> None:
    """Run one file intake scan: torrent move, audio enqueue, ttml enqueue, pretext normalize/request, extract enqueue, premium enqueue."""
    scan_torrent_watch_folder(ctx.config)
    scan_audio_files(ctx.config, ctx.audio_queue)

    ttml_watch_folder = os.fspath(ctx.config["TTML_WATCH_FOLDER"])
    if os.path.exists(ttml_watch_folder):
        for filename in os.listdir(ttml_watch_folder):
            if filename.lower().endswith(".ttml"):
                enqueue_if_absent(ctx.ttml_queue, os.path.join(ttml_watch_folder, filename))

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
            request_pretext_processing(ctx.pretext_queue, ctx.processed_files_global, ctx.processed_files_lock, file_path)

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
        "Queued: %d pretext, %d extract, %d premium, %d audio, %d ttml",
        ctx.pretext_queue.qsize(),
        ctx.extract_queue.qsize(),
        ctx.premium_extract_queue.qsize(),
        ctx.audio_queue.qsize(),
        ctx.ttml_queue.qsize(),
    )


def process_audio_pipeline(ctx: PipelineContext) -> None:
    current_thread = threading.current_thread()
    current_thread.name = "AudioPipeline-GPU"

    process_audio_queue(
        ctx.config,
        ctx.audio_queue,
        processing_lock=ctx.audio_processing_lock,
        done_folder_path=os.fspath(ctx.config["AUDIO_DONE_FOLDER"]),
    )


def process_ttml_pipeline(ctx: PipelineContext) -> None:
    watch_folder = os.path.abspath(os.fspath(ctx.config["TTML_WATCH_FOLDER"]))
    original_folder = os.fspath(ctx.config["ORIGINAL_FOLDER"])
    intervals = ctx.config.get("INTERVALS", {})
    wait_seconds = intervals.get("WAIT_SECONDS", 1.0)

    while not ctx.shutdown_flag.is_set():
        try:
            src = ctx.ttml_queue.get(timeout=wait_seconds)
        except Empty:
            continue

        try:
            src = os.path.abspath(os.fspath(src))
            if not os.path.exists(src):
                continue
            if (
                not src.lower().endswith(".ttml")
                or os.path.dirname(src) != watch_folder
            ):
                continue
            if not is_file_ready(src, wait=wait_seconds):
                enqueue_if_absent(ctx.ttml_queue, src)
                continue

            if not acquire_file_lock(src):
                enqueue_if_absent(ctx.ttml_queue, src)
                continue

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
                    "TTML Pipeline: Error processing %s: %s",
                    os.path.basename(src),
                    e,
                )
            finally:
                release_file_lock(src)
                cleanup_file_lock(src)

        except Exception as e:
            logging.error("TTML Pipeline: Error processing queued file: %s", e)
        finally:
            ctx.ttml_queue.task_done()


def process_wikilink_cleaning(ctx: PipelineContext) -> None:
    intervals = ctx.config.get("INTERVALS", {})
    scan_seconds = intervals.get("SCAN_SECONDS", 60)
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
            pass

        if ctx.shutdown_flag.wait(scan_seconds):
            return
