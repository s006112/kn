"""
Responsibility:
p_orchestrator.py 負責組裝與啟動； p_pipelines.py 負責做事。
Coordinate pipeline startup and lifecycle by loading prompt strings into a config dict, creating a `PipelineContext`, starting worker threads, and running a watchdog `Observer` for file events.

Used by:
* whisper/p.py

Pipelines:
- config -> read prompts -> create context -> start threads -> start watchdog -> monitor

Invariants:
- `CURRENT_CONTEXT` is set by `start_system()` after context creation and cleared by `stop_system()`.
- `start_system()` validates `cfg` is non-None and mutates it by setting `PRETEXT_PROMPT`, `EXTRACT_PROMPT`, and `DISTILL_PROMPT`.
- Worker threads created here are started as daemon threads.

Out of scope:
- Implementing pipeline worker logic (handled by `p_pipelines.py` and related modules).
- Parsing or generating prompt content beyond reading prompt files.
- Providing a process manager; shutdown is limited to setting a flag and stopping the observer.
"""

import logging
import os
import threading
import time
from typing import Any, Dict, NamedTuple, Optional

from watchdog.observers import Observer
from p_context import PipelineContext, create_pipeline_context
from p_pipelines import (
    create_pipeline_handlers,
    periodic_file_scanner,
    process_audio_pipeline,
    process_extract_queue,
    process_premium_extract_queue,
    process_pretext_queue,
    process_ttml_pipeline,
    process_wikilink_cleaning,
    scan_existing_files,
)
from utils_unlink import setup_wikilink_cleaner_logging
from utils_files import read_prompt_file


# Public API surface used by `p.py` and programmatic callers.
CURRENT_CONTEXT: Optional[PipelineContext] = None


class SystemHandles(NamedTuple):
    """Holds runtime handles for stopping and status checks."""

    context: PipelineContext
    threads: Dict[str, threading.Thread]
    observer: Any


def ensure_directories(cfg: Dict[str, Any]) -> None:
    """
    Purpose:
    Ensure required output directories exist before starting pipelines.
    Inputs:
    cfg: Configuration dictionary containing required folder paths.
    Outputs:
    None.
    Side effects:
    Creates directories at `ORIGINAL_FOLDER`, `AUDIO_DONE_FOLDER`, `LINK_BACKUP_FOLDER`, `FAIL_FOLDER` if missing.
    Failure modes:
    KeyError if required keys are missing; OSError on filesystem failures.
    """
    os.makedirs(cfg["ORIGINAL_FOLDER"], exist_ok=True)
    os.makedirs(cfg["AUDIO_DONE_FOLDER"], exist_ok=True)
    os.makedirs(cfg["LINK_BACKUP_FOLDER"], exist_ok=True)
    os.makedirs(cfg["FAIL_FOLDER"], exist_ok=True)


def start_system(cfg: Optional[Dict[str, Any]] = None) -> SystemHandles:
    """
    Purpose:
    Start all pipeline threads and watchdog observers and return runtime handles.
    Inputs:
    cfg: Configuration dictionary; must be non-None and include required folder keys used by this module.
    Outputs:
    `SystemHandles` containing the created `PipelineContext`, threads mapping, and watchdog observer.
    Side effects:
    Mutates `cfg` by setting `PRETEXT_PROMPT`, `EXTRACT_PROMPT`, `DISTILL_PROMPT`; creates directories; starts daemon threads; starts a watchdog observer; sets global `CURRENT_CONTEXT`.
    Failure modes:
    ValueError when `cfg` is None; KeyError for missing config keys; exceptions from prompt file reading, watchdog setup, or thread start.
    """
    if cfg is None:
        raise ValueError("Configuration dictionary is required.")

    cfg["PRETEXT_PROMPT"] = read_prompt_file("prompt_pretext.txt")
    cfg["EXTRACT_PROMPT"] = read_prompt_file("prompt_extract.txt")
    cfg["DISTILL_PROMPT"] = read_prompt_file("prompt_distill.txt")

    setup_wikilink_cleaner_logging(logging.getLogger())
    ensure_directories(cfg)

    ctx = create_pipeline_context(cfg)
    global CURRENT_CONTEXT
    CURRENT_CONTEXT = ctx

    (
        pretext_handler,
        pretext_processor,
        extract_handler,
        premium_extract_handler,
    ) = create_pipeline_handlers(ctx)

    threads: Dict[str, threading.Thread] = {
        "TTMLPipeline": threading.Thread(
            target=process_ttml_pipeline,
            args=(ctx,),
            daemon=True,
            name="TTMLPipeline",
        ),
        "TextPipeline-Pretext": threading.Thread(
            target=process_pretext_queue,
            args=(ctx, pretext_processor),
            daemon=True,
            name="TextPipeline-Pretext",
        ),
        "TextPipeline-Extract": threading.Thread(
            target=process_extract_queue,
            args=(ctx, extract_handler),
            daemon=True,
            name="TextPipeline-Extract",
        ),
        "TextPipeline-PremiumExtract": threading.Thread(
            target=process_premium_extract_queue,
            args=(ctx, premium_extract_handler),
            daemon=True,
            name="TextPipeline-PremiumExtract",
        ),
        "AudioPipeline-GPU": threading.Thread(
            target=process_audio_pipeline,
            args=(ctx,),
            daemon=True,
            name="AudioPipeline-GPU",
        ),
        "PeriodicScanner": threading.Thread(
            target=periodic_file_scanner,
            args=(ctx,),
            daemon=True,
            name="PeriodicScanner",
        ),
        "WikilinkCleaner": threading.Thread(
            target=process_wikilink_cleaning,
            args=(ctx,),
            daemon=True,
            name="WikilinkCleaner",
        ),
    }

    for t in threads.values():
        t.start()

    scan_existing_files(ctx)

    observer = Observer()
    observer.schedule(
        pretext_handler,
        os.fspath(cfg["PRETEXT_WATCH_FOLDER"]),
        recursive=False,
    )
    observer.schedule(
        extract_handler,
        os.fspath(cfg["EXTRACT_WATCH_FOLDER"]),
        recursive=False,
    )
    observer.schedule(
        premium_extract_handler,
        os.fspath(cfg["PREMIUM_WATCH_FOLDER"]),
        recursive=False,
    )
    observer.start()

    return SystemHandles(context=ctx, threads=threads, observer=observer)


def stop_system(handles: SystemHandles) -> None:
    """
    Purpose:
    Request a graceful shutdown by setting the shared shutdown flag and stopping the watchdog observer.
    Inputs:
    handles: `SystemHandles` returned by `start_system()`.
    Outputs:
    None.
    Side effects:
    Sets `handles.context.shutdown_flag`; attempts to stop/join the observer; clears global `CURRENT_CONTEXT`.
    Failure modes:
    AttributeError/TypeError if `handles` is invalid; observer stop/join errors are suppressed.
    """
    handles.context.shutdown_flag.set()
    try:
        handles.observer.stop()
        handles.observer.join()
    except Exception:
        pass
    finally:
        global CURRENT_CONTEXT
        CURRENT_CONTEXT = None


def system_status() -> Dict[str, Any]:
    """
    Purpose:
    Report current queue sizes and wikilink cleaner stats for the running system.
    Inputs:
    None.
    Outputs:
    Dictionary with `queues` and `wikilink_cleaner` sections derived from `CURRENT_CONTEXT`.
    Side effects:
    None.
    Failure modes:
    RuntimeError when the system has not been started (`CURRENT_CONTEXT` is None).
    """
    if CURRENT_CONTEXT is None:
        raise RuntimeError("System context not initialized.")
    ctx = CURRENT_CONTEXT
    return {
        "queues": {
            "pretext": ctx.pretext_queue.qsize(),
            "extract": ctx.extract_queue.qsize(),
            "premium_extract": ctx.premium_extract_queue.qsize(),
        },
        "wikilink_cleaner": {
            "last_run": ctx.wikilink_cleaning_stats["last_run"],
            "cycle_count": ctx.wikilink_cleaning_stats["cycle_count"],
        },
    }


def main(cfg: Optional[Dict[str, Any]] = None) -> None:
    """
    Purpose:
    Run the orchestrator main loop: start the system and periodically log queue status until interrupted.
    Inputs:
    cfg: Configuration dictionary; must be non-None.
    Outputs:
    None.
    Side effects:
    Starts pipelines via `start_system()`; logs status; sleeps in a loop; on KeyboardInterrupt calls `stop_system()`.
    Failure modes:
    ValueError when `cfg` is None; RuntimeError if `CURRENT_CONTEXT` becomes None during monitoring.
    """
    logging.info(
        "Starting: TTML + Text + Audio + WikilinkCleaner"
    )
    handles = start_system(cfg)

    logging.info("TTML: Independent subtitle file processing")
    logging.info("Text: Pretext → Extract/Premium Extract")

    try:
        while True:
            ctx = CURRENT_CONTEXT
            if ctx is None:
                raise RuntimeError("System context not initialized.")
            total_queues = (
                ctx.pretext_queue.qsize()
                + ctx.extract_queue.qsize()
                + ctx.premium_extract_queue.qsize()
            )
            if total_queues > 5:
                logging.info(
                    "System Status - Text queues: Pretext: %d, Extract: %d, Premium Extract: %d",
                    ctx.pretext_queue.qsize(),
                    ctx.extract_queue.qsize(),
                    ctx.premium_extract_queue.qsize(),
                )

            time.sleep(300)
    except KeyboardInterrupt:
        stop_system(handles)
        logging.info("4-pipeline independent parallel system stopped")


if __name__ == "__main__":
    main()
