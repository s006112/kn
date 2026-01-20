"""
Responsibility:
Run pipeline worker loops for pretext/extract/premium, audio, TTML, and wikilink
cleanup; manage queue scanning, enqueueing, and file lock coordination for the
orchestrator.

Used by:
* whisper/p_orchestrator.py

Pipelines:
- scan -> enqueue -> lock -> process -> finalize

Invariants:
- Queue consumers requeue files when file lock acquisition fails.
- Queue consumers defer lower-priority work when higher-priority queues are non-empty.

Out of scope:
- Constructing PipelineContext or application configuration.
- Implementing the underlying text/audio/TTML processing logic.
"""

import logging
import os
import sys
import threading
import time
from pathlib import Path
from queue import Queue
from typing import Any, Callable, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from p_context import PipelineContext
from p_pretext import PretextHandler, PretextProcessor, request_pretext_processing
from p_extract import ExtractHandler, PremiumExtractHandler
from p_ttml import handle_ttml, is_file_ready
from p_audio import process_audio_queue
from utils_unlink import clean_dead_links
from utils_files import get_next_available_filename, safe_rename
from utils_text import sanitize_and_trim_filename
from helper.utils_llm import LLMPermanentFailure
from utils_lock_registry import (
    acquire_file_lock,
    release_file_lock,
    cleanup_file_lock,
    file_lock,
    get_file_lock_functions,
)


def create_pipeline_handlers(
    ctx: PipelineContext,
) -> Tuple[PretextHandler, PretextProcessor, ExtractHandler, PremiumExtractHandler]:
    """
    Purpose:
    Build and return pipeline handler instances that share the provided context.
    Inputs:
    - ctx: PipelineContext with config and queues.
    Outputs:
    - Tuple of (PretextHandler, PretextProcessor, ExtractHandler, PremiumExtractHandler).
    Side effects:
    - Instantiates handler objects.
    Failure modes:
    - Propagates exceptions from handler constructors.
    """
    pretext_handler = PretextHandler(ctx)
    pretext_processor = PretextProcessor(ctx)
    extract_handler = ExtractHandler(ctx.config, ctx.extract_queue)
    premium_extract_handler = PremiumExtractHandler(
        ctx.config, ctx.premium_extract_queue
    )
    return pretext_handler, pretext_processor, extract_handler, premium_extract_handler


def enqueue_if_absent(queue: Queue, path: str) -> None:
    """
    Purpose:
    Enqueue a path if it is not already present in the queue.
    Inputs:
    - queue: Queue instance to receive the path.
    - path: File path to enqueue.
    Outputs:
    - None.
    Side effects:
    - May add an item to the queue.
    Failure modes:
    - Propagates exceptions from queue operations.
    """
    if path not in list(queue.queue):
        queue.put(path)


def _should_defer_processing(ctx: PipelineContext, method_name: str) -> bool:
    """
    Purpose:
    Decide whether to defer processing based on queue priority.
    Inputs:
    - ctx: PipelineContext with queues.
    - method_name: Handler method name used to infer priority.
    Outputs:
    - True when processing should be deferred, otherwise False.
    Side effects:
    - None.
    Failure modes:
    - None.
    """
    if method_name == "process_pretext":
        return (not ctx.extract_queue.empty()) or (not ctx.premium_extract_queue.empty())
    if method_name == "process_premium_extract":
        return not ctx.extract_queue.empty()
    return False


def process_queue(
    ctx: PipelineContext,
    queue: Queue,
    handler: Any,
    method_name: str,
) -> None:
    """
    Purpose:
    Consume a queue in a loop, applying file locking and invoking the handler method.
    Inputs:
    - ctx: PipelineContext with locks and queues.
    - queue: Queue to consume.
    - handler: Handler instance providing the target method.
    - method_name: Name of the handler method to call.
    Outputs:
    - None.
    Side effects:
    - Calls handler methods, acquires file locks, logs errors, and sleeps between cycles.
    Failure modes:
    - Logs and continues on exceptions from processing or queue operations.
    """
    process = getattr(handler, method_name)
    while True:
        try:
            if queue.empty() or _should_defer_processing(ctx, method_name):
                time.sleep(0.5)
                continue
            file_path = queue.get()
            with file_lock(file_path) as locked:
                if not locked:
                    queue.put(file_path)
                    queue.task_done()
                    time.sleep(1)
                    continue
                try:
                    with ctx.text_processing_lock:
                        process(file_path, get_next_available_filename)
                        processed = getattr(handler, "processed_files", None)
                        if processed and file_path in processed:
                            processed.discard(file_path)
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
        time.sleep(0.5)


def process_pretext_queue(ctx: PipelineContext, processor: PretextProcessor) -> None:
    """
    Purpose:
    Run the pretext processing queue worker loop.
    Inputs:
    - ctx: PipelineContext with queues and locks.
    - processor: PretextProcessor instance.
    Outputs:
    - None.
    Side effects:
    - Consumes ctx.pretext_queue and invokes processor logic.
    Failure modes:
    - Same as process_queue.
    """
    process_queue(ctx, ctx.pretext_queue, processor, "process_pretext")


def process_extract_queue(ctx: PipelineContext, handler: ExtractHandler) -> None:
    """
    Purpose:
    Run the extract processing queue worker loop.
    Inputs:
    - ctx: PipelineContext with queues and locks.
    - handler: ExtractHandler instance.
    Outputs:
    - None.
    Side effects:
    - Consumes ctx.extract_queue and invokes handler logic.
    Failure modes:
    - Same as process_queue.
    """
    process_queue(ctx, ctx.extract_queue, handler, "process_extract")


def process_premium_extract_queue(
    ctx: PipelineContext, handler: PremiumExtractHandler
) -> None:
    """
    Purpose:
    Run the premium extract processing queue worker loop.
    Inputs:
    - ctx: PipelineContext with queues and locks.
    - handler: PremiumExtractHandler instance.
    Outputs:
    - None.
    Side effects:
    - Consumes ctx.premium_extract_queue and invokes handler logic.
    Failure modes:
    - Same as process_queue.
    """
    process_queue(ctx, ctx.premium_extract_queue, handler, "process_premium_extract")


def list_matching_files(folder: str, predicate: Callable[[str], bool]) -> set[str]:
    """
    Purpose:
    Return a set of file paths in a folder that match the provided predicate.
    Inputs:
    - folder: Directory path to scan.
    - predicate: Function applied to each filename.
    Outputs:
    - Set of matching file paths.
    Side effects:
    - Reads the filesystem.
    Failure modes:
    - Propagates exceptions from os.listdir.
    """
    if not os.path.exists(folder):
        return set()
    return {
        os.path.join(folder, fn) for fn in os.listdir(folder) if predicate(fn)
    }


def scan_existing_files(ctx: PipelineContext) -> None:
    """
    Purpose:
    Scan watch folders at startup and enqueue eligible files.
    Inputs:
    - ctx: PipelineContext with config and queues.
    Outputs:
    - None.
    Side effects:
    - Renames files, enqueues work, and logs queue counts.
    Failure modes:
    - Logs rename errors and continues; may propagate other filesystem errors.
    """
    pretext_watch_folder = os.fspath(ctx.config["PRETEXT_WATCH_FOLDER"])
    extract_watch_folder = os.fspath(ctx.config["EXTRACT_WATCH_FOLDER"])
    premium_watch_folder = os.fspath(ctx.config["PREMIUM_WATCH_FOLDER"])
    pretext_suffix = str(ctx.config["PRETEXT_SUFFIX"]).lower()
    extract_suffix = str(ctx.config["EXTRACT_SUFFIX"]).lower()

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

        if filename_lower.endswith(pretext_suffix) and not filename_lower.endswith(extract_suffix):
            request_pretext_processing(ctx, file_path)

    for filename in os.listdir(extract_watch_folder):
        if filename.lower().endswith(extract_suffix):
            file_path = os.path.join(extract_watch_folder, filename)
            enqueue_if_absent(ctx.extract_queue, file_path)

    for filename in os.listdir(premium_watch_folder):
        if filename.lower().endswith(extract_suffix):
            file_path = os.path.join(premium_watch_folder, filename)
            enqueue_if_absent(ctx.premium_extract_queue, file_path)

    logging.info(
        "Queued: %d pretext, %d extract, %d premium",
        ctx.pretext_queue.qsize(),
        ctx.extract_queue.qsize(),
        ctx.premium_extract_queue.qsize(),
    )


def periodic_file_scanner(ctx: PipelineContext) -> None:
    """
    Purpose:
    Periodically scan watch folders and enqueue newly discovered files.
    Inputs:
    - ctx: PipelineContext with config and queues.
    Outputs:
    - None.
    Side effects:
    - Scans directories, enqueues work, and sleeps between cycles.
    Failure modes:
    - Logs errors and continues.
    """
    pretext_watch_folder = os.fspath(ctx.config["PRETEXT_WATCH_FOLDER"])
    extract_watch_folder = os.fspath(ctx.config["EXTRACT_WATCH_FOLDER"])
    premium_watch_folder = os.fspath(ctx.config["PREMIUM_WATCH_FOLDER"])
    pretext_suffix = str(ctx.config["PRETEXT_SUFFIX"]).lower()
    extract_suffix = str(ctx.config["EXTRACT_SUFFIX"]).lower()

    processed = set()
    extract_done = set()
    premium_processed = set()

    while not ctx.shutdown_flag.is_set():
        try:
            time.sleep(60)

            current = list_matching_files(
                pretext_watch_folder,
                lambda f: f.lower().endswith(pretext_suffix)
                and not f.lower().endswith(extract_suffix),
            )
            for path in current - processed:
                request_pretext_processing(ctx, path)
            processed = current

            extract_current = list_matching_files(
                extract_watch_folder, lambda f: f.lower().endswith(extract_suffix)
            )
            for path in extract_current - extract_done:
                enqueue_if_absent(ctx.extract_queue, path)
            extract_done = extract_current

            premium_current = list_matching_files(
                premium_watch_folder, lambda f: f.lower().endswith(extract_suffix)
            )
            for path in premium_current - premium_processed:
                enqueue_if_absent(ctx.premium_extract_queue, path)
            premium_processed = premium_current

        except Exception as e:
            logging.error("Periodic scanner error: %s", e)
            time.sleep(60)


def process_audio_pipeline(ctx: PipelineContext) -> None:
    """
    Purpose:
    Run the audio processing worker using the GPU pipeline.
    Inputs:
    - ctx: PipelineContext with config and queues.
    Outputs:
    - None.
    Side effects:
    - Renames the current thread and processes audio queue items.
    Failure modes:
    - Propagates exceptions from process_audio_queue.
    """
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
    """
    Purpose:
    Run the TTML processing loop for subtitle files.
    Inputs:
    - ctx: PipelineContext with config and shutdown flag.
    Outputs:
    - None.
    Side effects:
    - Scans watch folder, acquires file locks, processes TTML files, and sleeps.
    Failure modes:
    - Logs errors and continues.
    """
    watch_folder = os.fspath(ctx.config["TTML_WATCH_FOLDER"])
    original_folder = os.fspath(ctx.config["ORIGINAL_FOLDER"])

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
                if not is_file_ready(src):
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

            time.sleep(2)

        except Exception as e:
            logging.error("TTML Pipeline: Error during scan: %s", e)
            time.sleep(5)


def process_wikilink_cleaning(ctx: PipelineContext) -> None:
    """
    Purpose:
    Periodically clean dead wikilinks in the configured sync folder.
    Inputs:
    - ctx: PipelineContext with config and shutdown flag.
    Outputs:
    - None.
    Side effects:
    - Modifies files under the sync folder and may create backups.
    Failure modes:
    - Suppresses exceptions from clean_dead_links.
    """
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

        for _ in range(2):
            if ctx.shutdown_flag.wait(30):
                return
