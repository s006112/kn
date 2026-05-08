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
# grok-4-1-fast-reasoning $0.2, grok-4-1-fast-non-reasoning $0.2, grok-4.20-0309-non-reasoning $2.0
MODEL_PRETEXT = "gpt-4.1-mini"
MODEL_DISTILL = "o3"
MODEL_EXTRACT_MATRIX = {
    "EXTRACT_WATCH_FOLDER": [
        "gpt-5.4-mini",
        "grok-4.20-non-reasoning",
        "gemini-3.1-pro-preview",  # gemini-3.1-flash-lite-preview
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

PIPELINE_CONFIG = {
    "TORRENT": True,
    "AUDIO": True,
    "TTML": True,
    "PRETEXT": True,
    "EXTRACT": True,
    "NOTES": True,
    "X_URL_DOWNLOAD": True,
}

CONFIG = {
    **PATH_CONFIG,
    "MODEL_PRETEXT": MODEL_PRETEXT,
    "MODEL_EXTRACT_MATRIX": MODEL_EXTRACT_MATRIX,
    "MODEL_DISTILL": MODEL_DISTILL,
    "PRETEXT_SUFFIX": ".txt",
    "EXTRACT_SUFFIX": ("_p.txt", ".md"),
    "INTERVALS": INTERVAL_CONFIG,
    "PIPELINES": PIPELINE_CONFIG,
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
    scan_existing_files,
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

FALSE_VALUES = {"0", "false", "no", "off", "disable", "disabled"}


class SystemHandles(NamedTuple):
    """Runtime handles returned by start_system."""
    context: PipelineContext
    threads: dict[str, threading.Thread]
    observer: Any


def as_bool(value: Any) -> bool:
    """Accept bool-like strings so pipeline flags are easy to override."""
    if isinstance(value, str):
        return value.strip().lower() not in FALSE_VALUES
    return bool(value)


def pipeline_settings(cfg: dict[str, Any]) -> dict[str, bool]:
    """Return complete pipeline flags, defaulting missing keys to enabled."""
    raw = {**PIPELINE_CONFIG, **cfg.get("PIPELINES", {})}
    return {key: as_bool(value) for key, value in raw.items()}


def pipeline_enabled(cfg: dict[str, Any], key: str) -> bool:
    """Check whether one pipeline is enabled."""
    return pipeline_settings(cfg).get(key, True)


def enabled_pipeline_names(cfg: dict[str, Any]) -> str:
    """Return enabled pipeline names for startup logging."""
    names = [key for key, enabled in pipeline_settings(cfg).items() if enabled]
    return ", ".join(names) if names else "none"


def ensure_directories(cfg: dict[str, Any]) -> None:
    """Create runtime folders required before workers start."""
    for key in REQUIRED_DIR_KEYS:
        Path(cfg[key]).mkdir(parents=True, exist_ok=True)


def start_thread(
    threads: dict[str, threading.Thread],
    name: str,
    target: Any,
    args: tuple[Any, ...],
) -> None:
    """Create and start one daemon worker thread."""
    thread = threading.Thread(target=target, args=args, daemon=True, name=name)
    threads[name] = thread
    thread.start()


def start_system(cfg: dict[str, Any] | None = None) -> SystemHandles:
    """Initialize prompts, context, workers, scans, and watchdog observers."""
    if cfg is None:
        raise ValueError("Configuration dictionary is required.")

    cfg = {
        **cfg,
        "PIPELINES": pipeline_settings(cfg),
        "PRETEXT_PROMPT": read_prompt_file("prompt_pretext.txt"),
        "EXTRACT_PROMPT": read_prompt_file("prompt_extract.txt"),
        "DISTILL_PROMPT": read_prompt_file("prompt_distill.txt"),
    }

    setup_wikilink_cleaner_logging(logging.getLogger())
    ensure_directories(cfg)

    ctx = create_pipeline_context(cfg)

    global CURRENT_CONTEXT
    CURRENT_CONTEXT = ctx

    text_enabled = pipeline_enabled(cfg, "PRETEXT") or pipeline_enabled(cfg, "EXTRACT")
    pretext_handler = extract_handler = premium_extract_handler = None

    if text_enabled:
        (
            pretext_handler,
            extract_handler,
            premium_extract_handler,
        ) = create_pipeline_handlers(ctx)

    threads: dict[str, threading.Thread] = {}

    if pipeline_enabled(cfg, "TTML"):
        start_thread(threads, "TTMLPipeline", process_ttml_pipeline, (ctx,))

    if pipeline_enabled(cfg, "PRETEXT"):
        start_thread(
            threads,
            "TextPipeline-Pretext",
            process_pretext_queue,
            (ctx,),
        )

    if pipeline_enabled(cfg, "EXTRACT"):
        start_thread(
            threads,
            "TextPipeline-Extract",
            process_extract_queue,
            (ctx, extract_handler),
        )
        start_thread(
            threads,
            "TextPipeline-PremiumExtract",
            process_premium_extract_queue,
            (ctx, premium_extract_handler),
        )

    if pipeline_enabled(cfg, "AUDIO"):
        start_thread(threads, "AudioPipeline-GPU", process_audio_pipeline, (ctx,))

    if pipeline_enabled(cfg, "TORRENT"):
        start_thread(threads, "PeriodicScanner", periodic_file_scanner, (ctx,))

    if pipeline_enabled(cfg, "NOTES"):
        start_thread(threads, "WikilinkCleaner", process_wikilink_cleaning, (ctx,))

    if pipeline_enabled(cfg, "X_URL_DOWNLOAD"):
        start_thread(threads, "XUrlDownloadPipeline", process_x_url_download_pipeline, (ctx,))

    if pipeline_enabled(cfg, "AUDIO") or pipeline_enabled(cfg, "TTML") or text_enabled:
        scan_existing_files(ctx)

    watch_specs = []
    if pipeline_enabled(cfg, "PRETEXT"):
        watch_specs.append((pretext_handler, "PRETEXT_WATCH_FOLDER"))
    if pipeline_enabled(cfg, "EXTRACT"):
        watch_specs.extend(
            (
                (extract_handler, "EXTRACT_WATCH_FOLDER"),
                (premium_extract_handler, "PREMIUM_WATCH_FOLDER"),
            )
        )

    observer = None
    if watch_specs:
        observer = Observer()
        for handler, folder_key in watch_specs:
            observer.schedule(handler, os.fspath(cfg[folder_key]), recursive=False)
        observer.start()

    logging.info("Enabled pipelines: %s", enabled_pipeline_names(cfg))

    return SystemHandles(context=ctx, threads=threads, observer=observer)


def stop_system(handles: SystemHandles) -> None:
    """Signal shutdown, stop the observer, and clear global context."""
    handles.context.shutdown_flag.set()

    try:
        if handles.observer is not None:
            handles.observer.stop()
            handles.observer.join()
    except Exception as exc:
        logging.warning("Failed to stop observer cleanly: %s", exc)
    finally:
        global CURRENT_CONTEXT
        CURRENT_CONTEXT = None


def system_status() -> dict[str, Any]:
    """Return pipeline flags, queue sizes, and wikilink cleaner counters."""
    if CURRENT_CONTEXT is None:
        raise RuntimeError("System context not initialized.")

    ctx = CURRENT_CONTEXT
    return {
        "pipelines": pipeline_settings(ctx.config),
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

    logging.info("Starting local file-processing system")
    handles = start_system(resolved_cfg)

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
        logging.info("Local file-processing system stopped")


if __name__ == "__main__":
    main()
