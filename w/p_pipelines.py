""" p_pipelines.py -
Runtime pipeline orchestration.

Used by:
- p.py
- p_h.py

Flows:
- scanner -> pretext / extract intake
- text queue -> file lock -> processor -> archive / fail
- audio queue -> gpu worker -> archive
- ytd worker -> read X.txt -> download -> remove completed URL
"""

import logging
import os
import threading
import time
from contextlib import contextmanager
from queue import Queue
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Set

from .p_pretext import process_pretext_file, request_pretext_processing
from .p_extract import ExtractProcessor, PremiumExtractProcessor
from .helper_files import get_next_available_filename, safe_rename
from .helper_text import sanitize_and_trim_filename
from helper.helper_llm import LLMPermanentFailure

_file_locks: Dict[str, threading.Lock] = {}
_file_locks_mutex = threading.Lock()

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
    scan_files: Callable[[PipelineContext], None] | None = None,
) -> None:
    intervals = ctx.config.get("INTERVALS", {})
    wait_seconds = intervals.get("WAIT_SECONDS", 1.0)
    scan_seconds = intervals.get("SCAN_SECONDS", 60)
    next_scan = time.monotonic()

    while True:
        if scan_files and time.monotonic() >= next_scan:
            try:
                scan_files(ctx)
            except Exception as e:
                logging.error("%s scan error: %s", method_name, e)
            next_scan = time.monotonic() + scan_seconds

        if queue.empty():
            time.sleep(wait_seconds)
            continue

        file_path = queue.get()
        try:
            with file_lock(file_path) as locked:
                if not locked:
                    queue.put(file_path)
                else:
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
        except Exception as e:
            logging.error("%s queue error: %s", method_name, e)
        finally:
            queue.task_done()

        time.sleep(wait_seconds)

def process_pretext_queue(ctx: PipelineContext) -> None:
    process_queue(ctx, ctx.pretext_queue, lambda path, _next: process_pretext_file(ctx.config, path, ctx.processed_files_global, ctx.processed_files_lock), "process_pretext", scan_pretext_files)


def process_extract_queue(ctx: PipelineContext, processor: ExtractProcessor) -> None:
    process_queue(ctx, ctx.extract_queue, processor.process_extract, "process_extract", scan_extract_files)


def process_premium_extract_queue(
    ctx: PipelineContext, processor: PremiumExtractProcessor
) -> None:
    process_queue(ctx, ctx.premium_extract_queue, processor.process_premium_extract, "process_premium_extract", scan_premium_extract_files)


def scan_pretext_files(ctx: PipelineContext) -> None:
    pretext_watch_folder = os.fspath(ctx.config["PRETEXT_WATCH_FOLDER"])
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


def scan_extract_files(ctx: PipelineContext) -> None:
    extract_watch_folder = os.fspath(ctx.config["EXTRACT_WATCH_FOLDER"])
    extract_suffixes = tuple(
        str(s).lower() for s in ctx.config["EXTRACT_SUFFIX"] if str(s)
    )

    for filename in os.listdir(extract_watch_folder):
        filename_lower = filename.lower()
        if any(filename_lower.endswith(s) for s in extract_suffixes):
            file_path = os.path.join(extract_watch_folder, filename)
            enqueue_if_absent(ctx.extract_queue, file_path)


def scan_premium_extract_files(ctx: PipelineContext) -> None:
    premium_watch_folder = os.fspath(ctx.config["PREMIUM_WATCH_FOLDER"])
    extract_suffixes = tuple(
        str(s).lower() for s in ctx.config["EXTRACT_SUFFIX"] if str(s)
    )

    for filename in os.listdir(premium_watch_folder):
        filename_lower = filename.lower()
        if any(filename_lower.endswith(s) for s in extract_suffixes):
            file_path = os.path.join(premium_watch_folder, filename)
            enqueue_if_absent(ctx.premium_extract_queue, file_path)


def file_scanner(ctx: PipelineContext) -> None:
    pass
