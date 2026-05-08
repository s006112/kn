"""
p.py

Responsibility
Start and supervise the local file-processing system that watches configured folders,
loads prompts, creates pipeline context, starts queue workers, schedules watchdog
handlers, exposes status, and shuts the system down.

Used by:
* w/tool_wikilink_cleaner.py

Pipelines:
- torrent watch folder scan -> torrent detection -> file lock -> safe move -> w folder
- audio watch -> audio queue -> wav convert -> transcribe -> text write -> audio archive
- ttml watch -> ready check -> file lock -> ttml convert -> text write -> ttml archive
- pretext watch -> pretext queue -> llm pretext -> write outputs -> pretext archive
- extract watch -> extract queue -> llm extract -> merge markdown -> distill -> extract archive
- notes watch -> unlink clean -> link backup

"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, NamedTuple

from watchdog.observers import Observer

# The w package still contains modules that import sibling files by bare name.
sys.path.insert(0, os.fspath(Path(__file__).resolve().parent / "w"))

# sonar $1, sonar-pro $15, sonar-reasoning-pro $8
# gemini-2.5-flash-lite-preview-09-2025 $0.4, gemini-3.1-flash-lite-preview $1.5, gemini-3-pro-preview $12, 
# gpt-5-mini, gpt-5-nano, gpt-4.1-mini, gpt-4.1-nano, gpt-4o-mini, o1-mini, o3-mini, o4-mini,
# gpt-5.4 $15, gpt-5.2 $14, gpt-5.1 $10, gpt-4.1 $8, gpt-4o, o1 $60, o3 $8,
MODEL_PRETEXT = "gpt-4.1-mini"
MODEL_DISTILL = "o3"
MODEL_EXTRACT_MATRIX = {
    "EXTRACT_WATCH_FOLDER": [
        "gpt-5.4-mini",
        "grok-4.20-non-reasoning", # grok-4.3, grok-4-1-fast-non-reasoning
        "gemini-3.1-pro-preview", # gemini-3.1-flash-lite-preview

    ],
    "PREMIUM_WATCH_FOLDER": [
        "gpt-5.4",
    ],
}

WHISPER_FOLDER = Path("/desktop/Sync/Whisper")
WATCH_FOLDER = Path("/desktop")
AUDIO_DONE_FOLDER = Path("/desktop/YT1")
OBSIDIAN_SYNC_FOLDER = Path("/desktop/Obsidian/O_2025")

TTML_WATCH_FOLDER = WATCH_FOLDER
EXTRACT_WATCH_FOLDER = WATCH_FOLDER
AUDIO_TRANSCRIBED_TXT_FOLDER = WATCH_FOLDER

PATH_CONFIG = {
    "WATCH_FOLDER": WATCH_FOLDER,
    "WHISPER_FOLDER": WHISPER_FOLDER,
    "TTML_WATCH_FOLDER": TTML_WATCH_FOLDER,
    "EXTRACT_WATCH_FOLDER": EXTRACT_WATCH_FOLDER,
    "AUDIO_TRANSCRIBED_TXT_FOLDER": AUDIO_TRANSCRIBED_TXT_FOLDER,
    "AUDIO_WATCH_FOLDERS": (
        WATCH_FOLDER,
        WHISPER_FOLDER,
    ),
    "AUDIO_DONE_FOLDER": AUDIO_DONE_FOLDER,
    "PRETEXT_WATCH_FOLDER": WATCH_FOLDER,    # WHISPER_FOLDER, WATCH_FOLDER
    "PREMIUM_WATCH_FOLDER": WHISPER_FOLDER / "Fail" / "p",
    "PRETEXT_DONE_FOLDER": WHISPER_FOLDER / "_p",
    "ARCHIVE_FOLDER": WHISPER_FOLDER / "_p",
    "ORIGINAL_FOLDER": WHISPER_FOLDER / "_p" / "Raw",
    "EXTRACT_FOLDER": WHISPER_FOLDER / "_p" / "Extract",
    "LINK_BACKUP_FOLDER": WHISPER_FOLDER / "_p" / "link_backup",
    "FAIL_FOLDER": WHISPER_FOLDER / "Fail",
    "OBSIDIAN_SYNC_FOLDER": OBSIDIAN_SYNC_FOLDER,
    "X_URL_LIST_FILE": WHISPER_FOLDER / "X" / "X.txt",
    "DOWNLOAD_TARGET_FOLDER": WHISPER_FOLDER / "X",
}

INTERVAL_CONFIG = {
    # folder / file scan intervals
    "PERIODIC_SCAN_SECONDS": 60,          # pretext/extract/premium/torrent backup scan
    "DOWNLOAD_SCAN_SECONDS": 30,          # x.txt / X.txt URL downloader scan
    "AUDIO_IDLE_SCAN_SECONDS": 60,        # audio queue empty -> rescan later
    "TTML_SCAN_SECONDS": 2,               # TTML folder polling
    "WIKILINK_CLEAN_SECONDS": 60,         # main p.py wikilink cleaner

    # queue worker pacing
    "TEXT_QUEUE_IDLE_SECONDS": 0.5,       # pretext/extract/premium queue empty
    "TEXT_QUEUE_LOOP_SECONDS": 0.5,       # sleep after text queue loop
    "FILE_LOCK_RETRY_SECONDS": 1,         # lock miss -> requeue delay

    # readiness / error backoff
    "FILE_READY_STABILITY_SECONDS": 1.0,  # TTML size stable check
    "PIPELINE_ERROR_BACKOFF_SECONDS": 5,  # audio/TTML error sleep
    "SCAN_ERROR_BACKOFF_SECONDS": 60,     # periodic scanner error sleep

    # monitoring only
    "STATUS_LOG_SECONDS": 300,            # orchestrator status log loop

    # LLM/API timing
    "LLM_TIMEOUT_SECONDS": 90,
    "LLM_RETRY_DELAY_SECONDS": 10,
    "LLM_MAX_RETRIES": 2,

    # ytd / downloader network timing
    "X_RESOLVE_TIMEOUT_SECONDS": 20,

    # standalone tool only, if we want it shared too
    "STANDALONE_WIKILINK_CLEAN_SECONDS": 120,
}

CONFIG = {
    **PATH_CONFIG,
    "MODEL_PRETEXT": MODEL_PRETEXT,
    "MODEL_EXTRACT_MATRIX": MODEL_EXTRACT_MATRIX,
    "MODEL_DISTILL": MODEL_DISTILL,
    "PRETEXT_SUFFIX": ".txt",
    "EXTRACT_SUFFIX": ("_p.txt", ".md"),
    "INTERVALS": INTERVAL_CONFIG,
    # Prompt files are loaded at startup so importing CONFIG performs no prompt I/O.
    "PRETEXT_PROMPT": None,
    "EXTRACT_PROMPT": None,
    "DISTILL_PROMPT": None,
}

os.environ["PYTORCH_ALLOC_CONF"] = "max_split_size_mb:128,backend:native"
os.environ["PYTORCH_NO_CUDA_MEMORY_CACHING"] = "0"

from w.p_context import PipelineContext, create_pipeline_context
from w.p_pipelines import (
    create_pipeline_handlers,
    periodic_file_scanner,
    process_audio_pipeline,
    process_extract_queue,
    process_premium_extract_queue,
    process_pretext_queue,
    process_ttml_pipeline,
    process_wikilink_cleaning,
    process_x_url_download_pipeline,
    file_scanner,
)
from w.utils_files import read_prompt_file
from w.utils_unlink import setup_wikilink_cleaner_logging


class UTFStreamHandler(logging.StreamHandler):
    """Write formatted log records to a byte stream using UTF-8."""
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            stream = self.stream
            stream.buffer.write(msg.encode("utf-8"))
            stream.buffer.write(self.terminator.encode("utf-8"))
            self.flush()
        except Exception:
            self.handleError(record)


LOG_DIR = Path(__file__).resolve().parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        RotatingFileHandler(
            LOG_DIR / "script.log",
            maxBytes=1 * 1024 * 1024,
            backupCount=2,
            encoding="utf-8",
        ),
        UTFStreamHandler(sys.stdout),
    ],
)

CURRENT_CONTEXT: PipelineContext | None = None

REQUIRED_DIR_KEYS = (
    "ORIGINAL_FOLDER",
    "AUDIO_DONE_FOLDER",
    "LINK_BACKUP_FOLDER",
    "FAIL_FOLDER",
)


class SystemHandles(NamedTuple):
    """Runtime handles returned by start_system."""
    context: PipelineContext
    threads: dict[str, threading.Thread]
    observer: Any


def ensure_directories(cfg: dict[str, Any]) -> None:
    """Create runtime folders required before workers start."""
    for key in REQUIRED_DIR_KEYS:
        Path(cfg[key]).mkdir(parents=True, exist_ok=True)


def start_system(cfg: dict[str, Any] | None = None) -> SystemHandles:
    """Initialize prompts, context, workers, scans, and watchdog observers."""
    if cfg is None:
        raise ValueError("Configuration dictionary is required.")

    cfg = {
        **cfg,
        "PRETEXT_PROMPT": read_prompt_file("prompt_pretext.txt"),
        "EXTRACT_PROMPT": read_prompt_file("prompt_extract.txt"),
        "DISTILL_PROMPT": read_prompt_file("prompt_distill.txt"),
    }

    setup_wikilink_cleaner_logging(logging.getLogger())
    ensure_directories(cfg)

    ctx = create_pipeline_context(cfg)

    global CURRENT_CONTEXT
    CURRENT_CONTEXT = ctx

    (
        pretext_handler,
        extract_handler,
        premium_extract_handler,
    ) = create_pipeline_handlers(ctx)

    thread_specs = (
        ("TTMLPipeline", process_ttml_pipeline, (ctx,)),
        ("TextPipeline-Pretext", process_pretext_queue, (ctx,)),
        ("TextPipeline-Extract", process_extract_queue, (ctx, extract_handler)),
        (
            "TextPipeline-PremiumExtract",
            process_premium_extract_queue,
            (ctx, premium_extract_handler),
        ),
        ("AudioPipeline-GPU", process_audio_pipeline, (ctx,)),
        ("PeriodicScanner", periodic_file_scanner, (ctx,)),
        ("WikilinkCleaner", process_wikilink_cleaning, (ctx,)),
        ("XUrlDownloadPipeline", process_x_url_download_pipeline, (ctx,)),
    )

    threads = {
        name: threading.Thread(
            target=target,
            args=args,
            daemon=True,
            name=name,
        )
        for name, target, args in thread_specs
    }

    for thread in threads.values():
        thread.start()

    file_scanner(ctx)

    observer = Observer()
    watch_specs = (
        (pretext_handler, "PRETEXT_WATCH_FOLDER"),
        (extract_handler, "EXTRACT_WATCH_FOLDER"),
        (premium_extract_handler, "PREMIUM_WATCH_FOLDER"),
    )

    for handler, folder_key in watch_specs:
        observer.schedule(handler, os.fspath(cfg[folder_key]), recursive=False)

    observer.start()

    return SystemHandles(context=ctx, threads=threads, observer=observer)


def stop_system(handles: SystemHandles) -> None:
    """Signal shutdown, stop the observer, and clear global context."""
    handles.context.shutdown_flag.set()

    try:
        handles.observer.stop()
        handles.observer.join()
    except Exception as exc:
        logging.warning("Failed to stop observer cleanly: %s", exc)
    finally:
        global CURRENT_CONTEXT
        CURRENT_CONTEXT = None


def system_status() -> dict[str, Any]:
    """Return queue sizes and wikilink cleaner counters for the active system."""
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


def main(cfg: dict[str, Any] | None = None) -> None:
    """Start the system and keep the supervising process alive."""
    resolved_cfg = cfg or CONFIG

    logging.info(
        "Starting: TTML + Text + Audio + WikilinkCleaner + XUrlDownloadPipeline"
    )

    handles = start_system(resolved_cfg)

    logging.info("TTML: Independent subtitle file processing")
    logging.info("Text: Pretext → Extract/Premium Extract")
    logging.info("Download: X.txt URL processing")

    intervals = handles.context.config.get("INTERVALS", {})
    status_log_seconds = intervals.get("STATUS_LOG_SECONDS", 300)

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

            time.sleep(status_log_seconds)

    except KeyboardInterrupt:
        pass
    finally:
        stop_system(handles)
        logging.info("4-pipeline independent parallel system stopped")


if __name__ == "__main__":
    main()
